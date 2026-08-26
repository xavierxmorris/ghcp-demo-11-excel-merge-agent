"""Orchestration: config validation, execution, and the audit manifest.

Two guarantees this module exists to provide:
  1. Config is validated BEFORE any data moves, so a bad agent edit fails fast
     with a precise message instead of producing a plausible-looking workbook.
  2. Every run emits a manifest (input hashes, per-step row counts, unresolved
     headers) so a reviewer can answer "where did this number come from?".
"""

from __future__ import annotations

import difflib
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .ingest import IngestError, SourceRead, read_source
from .schema import ResolutionReport, Schema, SchemaError, is_blank
from .steps import OPS, PipelineState, StepError, StepLog
from .yamlio import YamlError, load_yaml

REQUIRED_PARAMS: dict[str, tuple[str, ...]] = {
    "filter_rows": ("column", "operator"),
    "deduplicate": ("keys",),
    "merge_environments": ("left", "right", "keys"),
    "compute": ("target", "left", "right", "operator"),
    "derive_status": ("target", "rules"),
    "map_values": ("source", "target", "mapping"),
    "add_constant": ("target",),
    "sort": ("by",),
    "select_columns": ("columns",),
    "rename_output": ("mapping",),
}

OPTIONAL_PARAMS: dict[str, tuple[str, ...]] = {
    "filter_rows": ("value",),
    "deduplicate": ("order_by", "keep"),
    "merge_environments": ("how", "compare", "coalesce", "tolerance"),
    "compute": ("decimals",),
    "derive_status": ("default",),
    "map_values": ("default",),
    "add_constant": ("value",),
    "sort": ("ascending",),
    "select_columns": (),
    "rename_output": (),
}

COMMON_PARAMS = {"id", "op", "description", "applies_to"}


class ConfigError(ValueError):
    pass


@dataclass
class RunResult:
    frame: pd.DataFrame
    manifest: dict[str, Any]
    status_column: str | None
    output_path: Path
    exception_labels: list[str] = field(default_factory=list)
    main_sheet: str = "Result"

    @property
    def status_counts(self) -> dict[str, int]:
        if not self.status_column or self.status_column not in self.frame.columns:
            return {}
        counts = self.frame[self.status_column].value_counts(dropna=False)
        return {str(k): int(v) for k, v in counts.items()}

    @property
    def exceptions(self) -> pd.DataFrame:
        if not self.status_column or self.status_column not in self.frame.columns:
            return self.frame.iloc[0:0]
        if not self.exception_labels:
            return self.frame.iloc[0:0]
        mask = self.frame[self.status_column].isin(self.exception_labels)
        return self.frame.loc[mask].reset_index(drop=True)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_config(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    try:
        data = load_yaml(path) or {}
    except YamlError as exc:
        raise ConfigError(str(exc)) from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a YAML mapping at the top level.")
    _repair_yaml_bool_keys(data)
    return data


# YAML 1.1 turns bare `on`, `off`, `yes` and `no` into booleans, so a step
# written as `on: [request_id]` silently arrives as `{True: [...]}`. A quoted
# `'on'` arrives as the string instead. Accept both and rewrite to `keys`,
# rather than let it surface as a baffling "missing parameter".
_ON_KEY_ALIASES: tuple[Any, ...] = (True, "on", "On", "ON")
_OPS_TAKING_KEYS = {"deduplicate", "merge_environments"}


def _repair_yaml_bool_keys(cfg: dict[str, Any]) -> None:
    for step in cfg.get("steps") or []:
        if not isinstance(step, dict) or step.get("op") not in _OPS_TAKING_KEYS:
            continue
        if step.get("keys") is not None:
            continue
        for alias in _ON_KEY_ALIASES:
            if alias in step:
                step["keys"] = step.pop(alias)
                break


def validate_config(cfg: dict[str, Any]) -> list[str]:
    """Static checks that run before any file is opened. Returns warnings."""
    warnings: list[str] = []

    sources = cfg.get("sources") or {}
    if not sources:
        raise ConfigError("`sources` must define at least one source.")
    for name, scfg in sources.items():
        if not isinstance(scfg, dict) or not scfg.get("path"):
            raise ConfigError(f"Source {name!r} must be a mapping with a `path`.")

    steps = cfg.get("steps") or []
    if not steps:
        raise ConfigError("`steps` must define at least one step.")

    seen_ids: set[str] = set()
    merge_seen = False
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            raise ConfigError(f"Step #{index + 1} must be a mapping.")
        step_id = step.get("id")
        if not step_id:
            raise ConfigError(f"Step #{index + 1} is missing an `id`.")
        if step_id in seen_ids:
            raise ConfigError(f"Duplicate step id {step_id!r}. Step ids must be unique.")
        seen_ids.add(step_id)

        op = step.get("op")
        if op not in OPS:
            raise ConfigError(
                f"Step {step_id!r} uses unknown op {op!r}. Supported ops: {', '.join(sorted(OPS))}."
            )
        for param in REQUIRED_PARAMS.get(op, ()):
            if step.get(param) is None:
                raise ConfigError(f"Step {step_id!r} ({op}) is missing required parameter `{param}`.")

        # Reject unknown parameters. Without this, a typo such as `tolerence: 1000`
        # is accepted and the intended tolerance silently stays at 0.
        allowed = COMMON_PARAMS | set(REQUIRED_PARAMS.get(op, ())) | set(OPTIONAL_PARAMS.get(op, ()))
        for param in step:
            if param in allowed:
                continue
            suggestion = difflib.get_close_matches(str(param), sorted(allowed), n=1, cutoff=0.6)
            hint = f" Did you mean `{suggestion[0]}`?" if suggestion else ""
            raise ConfigError(
                f"Step {step_id!r} ({op}) has unknown parameter `{param}`.{hint} "
                f"Allowed: {', '.join(sorted(allowed))}."
            )

        if op == "merge_environments":
            if merge_seen:
                raise ConfigError(f"Step {step_id!r}: only one merge_environments step is supported per pipeline.")
            merge_seen = True
            for role in ("left", "right"):
                if step.get(role) not in sources:
                    raise ConfigError(
                        f"Step {step_id!r}: `{role}` is {step.get(role)!r}, which is not a declared source "
                        f"({', '.join(sorted(sources))})."
                    )
            overlap = sorted(set(step.get("compare") or []) & set(step.get("coalesce") or []))
            if overlap:
                raise ConfigError(
                    f"Step {step_id!r}: field(s) {overlap} appear in both `compare` and `coalesce`. "
                    "Coalescing removes the per-environment values that the comparison is evidence for."
                )
        elif step.get("applies_to") and merge_seen:
            raise ConfigError(
                f"Step {step_id!r} uses `applies_to` but runs after the merge. "
                "Per-source steps must be declared before merge_environments."
            )
        elif not step.get("applies_to") and not merge_seen and op not in {"merge_environments"}:
            raise ConfigError(
                f"Step {step_id!r} runs before the merge, so it must declare `applies_to` "
                f"naming one or more sources ({', '.join(sorted(sources))})."
            )

    if not merge_seen and len(sources) > 1:
        warnings.append("Multiple sources declared but no merge_environments step; only the merged frame is written.")

    output = cfg.get("output") or {}
    if not output.get("path"):
        raise ConfigError("`output.path` is required.")

    # Writing the output over one of the inputs would corrupt the next run and
    # make the manifest's input hash meaningless.
    output_name = Path(str(output["path"])).name.lower()
    for name, scfg in sources.items():
        if Path(str(scfg["path"])).name.lower() == output_name:
            raise ConfigError(
                f"`output.path` ({output['path']}) targets the same file as source {name!r}. "
                "Write output to a separate directory."
            )

    labels = output.get("exception_labels") or []
    if labels:
        classifiers = [s for s in steps if s.get("op") == "derive_status"]
        if not classifiers:
            raise ConfigError(
                "`output.exception_labels` is set but no `derive_status` step produces a classification."
            )
        known = {r.get("label") for s in classifiers for r in (s.get("rules") or [])}
        known |= {s.get("default") for s in classifiers}
        unknown_labels = sorted({str(x) for x in labels} - {str(k) for k in known if k is not None})
        if unknown_labels:
            raise ConfigError(
                f"`output.exception_labels` lists {unknown_labels}, which no rule or default in "
                f"`derive_status` can produce. Known labels: {sorted(str(k) for k in known if k)}."
            )
        produced = sorted({str(k) for k in known if k} - {str(x) for x in labels})
        if produced:
            warnings.append(
                f"Classification(s) {produced} are never routed to the Exceptions sheet. "
                "Add them to output.exception_labels if a human must action them."
            )

    return warnings


def run_pipeline(
    config_path: str | Path,
    schema_path: str | Path,
    base_dir: str | Path | None = None,
    strict: bool = True,
    allow_unparseable: bool = False,
    out: str | Path | None = None,
) -> RunResult:
    config_path, schema_path = Path(config_path), Path(schema_path)
    base = Path(base_dir) if base_dir else config_path.resolve().parent.parent

    cfg = load_config(config_path)
    warnings = validate_config(cfg)
    schema = Schema.load(schema_path)

    started = datetime.now(timezone.utc)
    frames: dict[str, pd.DataFrame] = {}
    reads: list[SourceRead] = []
    reports: list[ResolutionReport] = []

    for name, scfg in (cfg.get("sources") or {}).items():
        raw, meta = read_source(name, scfg, base)
        normalised, report = schema.apply(raw, source=name)
        frames[name] = normalised
        reads.append(meta)
        reports.append(report)

    blocking: list[str] = []
    for report in reports:
        if report.missing_required:
            blocking.append(f"source {report.source!r} is missing required field(s) {report.missing_required}")
        if report.duplicated:
            for canonical, headers in report.duplicated.items():
                blocking.append(
                    f"source {report.source!r} maps {headers} onto the same field {canonical!r}; "
                    "keep one column or add a distinct field to the schema"
                )
        if strict and report.unknown:
            blocking.append(
                f"source {report.source!r} has unrecognised header(s) {report.unknown}. "
                "Add them to config/schema.yaml aliases, or re-run with --no-strict to ignore them."
            )
        if report.coercion_failures and not allow_unparseable:
            detail = ", ".join(f"{col} ({count})" for col, count in sorted(report.coercion_failures.items()))
            blocking.append(
                f"source {report.source!r} has values that do not match their declared type: {detail}. "
                "An unparseable value becomes blank, and two blanks compare as EQUAL, so this would "
                "silently report a false match. Fix the export or the schema type, or pass "
                "--allow-unparseable to accept the risk (it is recorded in the manifest)."
            )

    # Blank join keys must be caught before any dedupe step, because
    # drop_duplicates treats all blanks as one group and would discard rows.
    for name, frame in frames.items():
        for key in schema.key:
            if key not in frame.columns:
                continue
            blanks = int(frame[key].map(is_blank).sum())
            if blanks:
                blocking.append(
                    f"source {name!r} has {blanks} row(s) with a blank {key!r}. "
                    "Blank keys join to each other and merge unrelated records."
                )

    if blocking:
        raise SchemaError("Schema validation failed:\n  - " + "\n  - ".join(blocking))

    state = PipelineState(frames=frames)
    for step in cfg["steps"]:
        handler = OPS[step["op"]]
        try:
            log = handler(state, step)
        except StepError:
            raise
        except Exception as exc:  # surface the offending step, not a bare traceback
            raise StepError(f"Step {step.get('id')!r} ({step.get('op')}) failed: {exc}") from exc
        state.logs.append(log)

    if state.merged is None:
        if len(frames) == 1:
            state.merged = next(iter(frames.values()))
        else:
            raise ConfigError("Pipeline produced no merged frame. Add a merge_environments step.")

    output_cfg = cfg.get("output") or {}
    exception_labels = [str(x) for x in (output_cfg.get("exception_labels") or [])]

    # If exceptions are configured, the classification must survive select_columns
    # and rename_output. Otherwise the Summary sheet reads "no classification"
    # and the CLI cheerfully reports "No exceptions" for a run that computed them.
    if exception_labels and state.status_column is None:
        raise ConfigError(
            "`output.exception_labels` is configured, but the classification column produced by "
            "`derive_status` was dropped by a later `select_columns` step. The Exceptions sheet "
            "would be silently empty. Add the classification column back to `select_columns`."
        )

    out_path = Path(out) if out is not None else Path(output_cfg["path"])
    if not out_path.is_absolute():
        out_path = base / out_path

    finished = datetime.now(timezone.utc)
    manifest = {
        "pipeline": {
            "name": cfg.get("name", config_path.stem),
            "version": cfg.get("version"),
            "config_path": str(config_path),
            "config_sha256": sha256_text(config_path.read_text(encoding="utf-8")),
            "schema_path": str(schema_path),
            "schema_sha256": sha256_text(schema_path.read_text(encoding="utf-8")),
            "schema_version": schema.version,
            "strict": strict,
            "allow_unparseable": allow_unparseable,
        },
        "run": {
            "started_utc": started.isoformat(timespec="seconds"),
            "finished_utc": finished.isoformat(timespec="seconds"),
            "duration_seconds": round((finished - started).total_seconds(), 3),
            "tool_version": "xlflow 0.1.0",
        },
        "sources": [r.to_dict() for r in reads],
        "header_resolution": [
            {
                "source": r.source,
                "mapped": r.mapping,
                "unknown": r.unknown,
                "duplicated": r.duplicated,
                "coercion_failures": r.coercion_failures,
            }
            for r in reports
        ],
        "steps": [log.to_dict() for log in state.logs],
        "warnings": warnings,
        "output": {"path": str(out_path), "rows": int(len(state.merged)), "columns": [str(c) for c in state.merged.columns]},
    }

    result = RunResult(
        frame=state.merged,
        manifest=manifest,
        status_column=state.status_column,
        output_path=out_path,
        exception_labels=exception_labels,
        main_sheet=str(output_cfg.get("main_sheet", "Result")),
    )
    manifest["summary"] = result.status_counts
    return result


def write_manifest(result: RunResult) -> Path:
    path = result.output_path.with_suffix(".manifest.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result.manifest, indent=2, default=str), encoding="utf-8")
    return path


__all__ = [
    "ConfigError",
    "IngestError",
    "RunResult",
    "SchemaError",
    "StepError",
    "StepLog",
    "load_config",
    "run_pipeline",
    "validate_config",
    "write_manifest",
]
