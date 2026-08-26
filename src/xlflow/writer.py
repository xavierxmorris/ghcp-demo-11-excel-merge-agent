"""Excel output: a reviewed workbook, not a data dump.

Sheets:
  <main>      the merged result, frozen header, autofilter, mismatch highlighting
  Summary     counts per classification
  Exceptions  only the rows a human must act on
  Run Log     the audit trail (hashes, per-step row counts, unresolved headers)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

HEADER_FILL = PatternFill("solid", start_color="FF1F3864")
HEADER_FONT = Font(color="FFFFFFFF", bold=True)

SEVERITY_FILL = {
    "HIGH": PatternFill("solid", start_color="FFF8CBAD"),
    "MEDIUM": PatternFill("solid", start_color="FFFFE699"),
    "LOW": PatternFill("solid", start_color="FFDDEBF7"),
}
OK_FILL = PatternFill("solid", start_color="FFE2EFDA")


def _excel_safe(df: pd.DataFrame) -> pd.DataFrame:
    """pandas nullable dtypes (Int64/Float64/boolean) write <NA> oddly; blank them."""
    out = df.copy()
    for col in out.columns:
        series = out[col]
        if pd.api.types.is_datetime64_any_dtype(series):
            # Excel cannot store tz-aware datetimes.
            try:
                if getattr(series.dtype, "tz", None) is not None:
                    series = series.dt.tz_localize(None)
            except (AttributeError, TypeError):
                pass
            out[col] = series
            continue
        if str(series.dtype) in {"Int64", "Float64", "boolean"} or series.dtype == object:
            out[col] = series.astype(object).where(series.notna(), None)
    return out


def _style_sheet(worksheet, df: pd.DataFrame, status_column: str | None, severity_column: str | None) -> None:
    worksheet.freeze_panes = "A2"
    ncols = len(df.columns)
    if ncols and len(df):
        worksheet.auto_filter.ref = f"A1:{get_column_letter(ncols)}{len(df) + 1}"

    for idx, name in enumerate(df.columns, start=1):
        cell = worksheet.cell(row=1, column=idx)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(vertical="center", wrap_text=True)

        body = df[name].astype(object).where(df[name].notna(), "")
        widest = max([len(str(name))] + [len(str(v)) for v in body.head(500)]) if len(df) else len(str(name))
        worksheet.column_dimensions[get_column_letter(idx)].width = min(max(widest + 3, 11), 42)

    colour_col = severity_column if severity_column in df.columns else status_column
    if colour_col and colour_col in df.columns:
        col_index = list(df.columns).index(colour_col) + 1
        for row_offset, value in enumerate(df[colour_col].tolist(), start=2):
            text = str(value).upper()
            fill = SEVERITY_FILL.get(text)
            if fill is None and text in {"OK", "NONE"}:
                fill = OK_FILL
            if fill is None:
                continue
            worksheet.cell(row=row_offset, column=col_index).fill = fill


def _find(df: pd.DataFrame, *candidates: str) -> str | None:
    lowered = {str(c).strip().lower(): str(c) for c in df.columns}
    for candidate in candidates:
        hit = lowered.get(candidate.strip().lower())
        if hit:
            return hit
    return None


def _manifest_rows(manifest: dict[str, Any]) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    pipeline, run = manifest.get("pipeline", {}), manifest.get("run", {})

    rows.append(("Pipeline", "name", str(pipeline.get("name"))))
    rows.append(("Pipeline", "config version", str(pipeline.get("version"))))
    rows.append(("Pipeline", "config sha256", str(pipeline.get("config_sha256"))[:16]))
    rows.append(("Pipeline", "schema version", str(pipeline.get("schema_version"))))
    rows.append(("Pipeline", "schema sha256", str(pipeline.get("schema_sha256"))[:16]))
    rows.append(("Pipeline", "strict headers", str(pipeline.get("strict"))))
    rows.append(("Run", "started (UTC)", str(run.get("started_utc"))))
    rows.append(("Run", "duration (s)", str(run.get("duration_seconds"))))
    rows.append(("Run", "tool", str(run.get("tool_version"))))

    for source in manifest.get("sources", []):
        label = f"Source: {source.get('source')}"
        rows.append((label, "env", str(source.get("env"))))
        rows.append((label, "file", str(source.get("path"))))
        rows.append((label, "sha256", str(source.get("sha256"))[:16]))
        rows.append((label, "rows read", str(source.get("rows_in"))))

    for res in manifest.get("header_resolution", []):
        label = f"Headers: {res.get('source')}"
        rows.append((label, "mapped", str(len(res.get("mapped") or {}))))
        if res.get("unknown"):
            rows.append((label, "UNRESOLVED", ", ".join(res["unknown"])))
        for column, count in (res.get("coercion_failures") or {}).items():
            rows.append((label, f"unparseable values in {column}", str(count)))

    for step in manifest.get("steps", []):
        rows.append((f"Step: {step.get('step_id')}", step.get("op", ""), step.get("detail", "")))

    for label, count in (manifest.get("summary") or {}).items():
        rows.append(("Summary", label, str(count)))

    for warning in manifest.get("warnings", []):
        rows.append(("Warning", "", str(warning)))

    rows.append(("Output", "rows", str((manifest.get("output") or {}).get("rows"))))
    return rows


def write_workbook(result) -> Path:
    df = _excel_safe(result.frame)
    status_column = result.status_column
    severity_column = _find(df, "severity", "Severity")

    out_path = Path(result.output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    summary_df = pd.DataFrame(
        sorted(result.status_counts.items(), key=lambda kv: (-kv[1], kv[0])),
        columns=["Classification", "Rows"],
    )
    if summary_df.empty:
        summary_df = pd.DataFrame([{"Classification": "(no classification step)", "Rows": len(df)}])

    exceptions_df = _excel_safe(result.exceptions)
    manifest_df = pd.DataFrame(_manifest_rows(result.manifest), columns=["Area", "Item", "Value"])

    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name=result.main_sheet, index=False)
        summary_df.to_excel(writer, sheet_name="Summary", index=False)
        exceptions_df.to_excel(writer, sheet_name="Exceptions", index=False)
        manifest_df.to_excel(writer, sheet_name="Run Log", index=False)

        _style_sheet(writer.sheets[result.main_sheet], df, status_column, severity_column)
        _style_sheet(writer.sheets["Summary"], summary_df, None, None)
        _style_sheet(writer.sheets["Exceptions"], exceptions_df, status_column, severity_column)
        _style_sheet(writer.sheets["Run Log"], manifest_df, None, None)

    return out_path
