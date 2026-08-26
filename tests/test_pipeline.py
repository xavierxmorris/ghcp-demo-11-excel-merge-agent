"""End-to-end: config validation, the full run, and reproducibility."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from xlflow.pipeline import ConfigError, load_config, run_pipeline, validate_config, write_manifest
from xlflow.schema import SchemaError
from xlflow.steps import StepError
from xlflow.writer import write_workbook

CONFIG = "config/pipeline.yaml"
SCHEMA = "config/schema.yaml"


def _run(ws, **kwargs):
    return run_pipeline(ws / CONFIG, ws / SCHEMA, base_dir=ws, **kwargs)


def test_end_to_end_classifications(clean_workspace):
    result = _run(clean_workspace)
    frame = result.frame.set_index("Request ID")

    assert len(frame) == 9
    assert frame.loc["REQ-1001", "Reconciliation Status"] == "OK"
    assert frame.loc["REQ-1003", "Reconciliation Status"] == "AMOUNT_MISMATCH"
    assert frame.loc["REQ-1005", "Reconciliation Status"] == "STATUS_MISMATCH"
    assert frame.loc["REQ-1009", "Reconciliation Status"] == "MISSING_IN_SVP"
    assert frame.loc["REQ-1010", "Reconciliation Status"] == "MISSING_IN_ESIT"


def test_cancelled_rows_are_filtered_out(clean_workspace):
    assert "REQ-1006" not in set(_run(clean_workspace).frame["Request ID"])


def test_duplicate_request_resolves_to_latest_submission(clean_workspace):
    """REQ-1002 appears twice in ESIT; the corrected later row must win."""
    frame = _run(clean_workspace).frame.set_index("Request ID")
    assert (frame.index == "REQ-1002").sum() == 1
    assert float(frame.loc["REQ-1002", "Amount (ESIT)"]) == pytest.approx(3250.00)
    assert frame.loc["REQ-1002", "Reconciliation Status"] == "OK"


def test_amount_delta_is_signed_esit_minus_svp(clean_workspace):
    frame = _run(clean_workspace).frame.set_index("Request ID")
    assert float(frame.loc["REQ-1003", "Amount Delta"]) == pytest.approx(500.0)


def test_high_severity_rows_sort_first(clean_workspace):
    severities = list(_run(clean_workspace).frame["Severity"])
    rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "NONE": 3}
    assert severities == sorted(severities, key=lambda s: rank[s])


def test_exceptions_exclude_ok_rows(clean_workspace):
    result = _run(clean_workspace)
    assert set(result.exceptions["Request ID"]) == {"REQ-1003", "REQ-1005", "REQ-1009", "REQ-1010"}
    assert "OK" not in set(result.exceptions["Reconciliation Status"])


def test_drifted_headers_block_the_run(drifted_workspace):
    """A rewritten query must fail loudly, naming the headers it could not resolve."""
    from make_samples import DRIFT_NEW_COLUMNS, DRIFT_RENAMES

    with pytest.raises(SchemaError) as excinfo:
        _run(drifted_workspace)

    message = str(excinfo.value)
    expected = list(DRIFT_RENAMES.values()) + list(DRIFT_NEW_COLUMNS)
    for header in expected:
        assert header in message, f"{header!r} should be reported as unresolved"
    assert "schema.yaml" in message


def test_no_strict_still_fails_when_a_compared_field_is_renamed(drifted_workspace):
    """--no-strict is NOT a way to ignore a rename of a field the pipeline compares.

    Dropping the unrecognised NET_AMOUNT_EX_VAT column leaves SVP with no
    `amount`, so the merge refuses rather than quietly reporting every row as
    an amount mismatch.
    """
    with pytest.raises(StepError, match="cannot compare 'amount'"):
        _run(drifted_workspace, strict=False)


def test_no_strict_tolerates_purely_additive_drift(additive_drift_workspace):
    """A new column the pipeline does not use is survivable - and still recorded."""
    from make_samples import DRIFT_NEW_COLUMNS

    result = _run(additive_drift_workspace, strict=False)
    unresolved = [u for res in result.manifest["header_resolution"] for u in res["unknown"]]
    for header in DRIFT_NEW_COLUMNS:
        assert header in unresolved
    assert result.status_counts["OK"] == 5


def test_manifest_records_inputs_and_every_step(clean_workspace):
    manifest = _run(clean_workspace).manifest
    assert {s["source"] for s in manifest["sources"]} == {"esit", "svp"}
    assert all(len(s["sha256"]) == 64 for s in manifest["sources"])
    assert [s["step_id"] for s in manifest["steps"]] == [
        "drop_cancelled", "dedupe_sources", "merge", "amount_delta",
        "classify", "severity", "order", "final_columns", "headers",
    ]
    assert manifest["summary"]["OK"] == 5


def test_two_runs_of_identical_input_agree(clean_workspace):
    first, second = _run(clean_workspace), _run(clean_workspace)
    pd.testing.assert_frame_equal(first.frame, second.frame)
    assert first.manifest["pipeline"]["config_sha256"] == second.manifest["pipeline"]["config_sha256"]


def test_workbook_has_all_four_sheets(clean_workspace):
    result = _run(clean_workspace)
    path = write_workbook(result)
    assert path.exists()
    assert pd.ExcelFile(path, engine="openpyxl").sheet_names == [
        "Reconciliation", "Summary", "Exceptions", "Run Log",
    ]


def test_manifest_json_is_written_next_to_workbook(clean_workspace):
    result = _run(clean_workspace)
    write_workbook(result)
    manifest_path = write_manifest(result)
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["summary"]["OK"] == 5


# --- config validation guards the agent against writing nonsense -------------

def _cfg(clean_workspace):
    return load_config(clean_workspace / CONFIG)


def test_unknown_op_is_rejected(clean_workspace):
    cfg = _cfg(clean_workspace)
    cfg["steps"][0]["op"] = "teleport_rows"
    with pytest.raises(ConfigError, match="unknown op"):
        validate_config(cfg)


def test_duplicate_step_id_is_rejected(clean_workspace):
    cfg = _cfg(clean_workspace)
    cfg["steps"].append(dict(cfg["steps"][0]))
    with pytest.raises(ConfigError, match="Duplicate step id"):
        validate_config(cfg)


def test_merge_referencing_undeclared_source_is_rejected(clean_workspace):
    cfg = _cfg(clean_workspace)
    next(s for s in cfg["steps"] if s["op"] == "merge_environments")["right"] = "prod"
    with pytest.raises(ConfigError, match="not a declared source"):
        validate_config(cfg)


def test_per_source_step_after_merge_is_rejected(clean_workspace):
    cfg = _cfg(clean_workspace)
    cfg["steps"].append({"id": "late", "op": "filter_rows", "applies_to": ["esit"],
                         "column": "status", "operator": "eq", "value": "OK"})
    with pytest.raises(ConfigError, match="must be declared before merge_environments"):
        validate_config(cfg)


@pytest.mark.parametrize("written_key", ["on", "'on'"])
def test_yaml_bare_on_key_is_repaired(tmp_path, written_key):
    """`on: [request_id]` is boolean True in YAML 1.1; `'on'` is a string. Accept both."""
    path = tmp_path / "legacy.yaml"
    path.write_text(
        "version: 1\n"
        "name: legacy\n"
        "sources:\n"
        "  esit: {path: inbox/esit.xlsx, env: ESIT}\n"
        "  svp: {path: inbox/svp.xlsx, env: SVP}\n"
        "output: {path: out/x.xlsx}\n"
        "steps:\n"
        "  - id: dedupe\n"
        "    op: deduplicate\n"
        "    applies_to: [esit, svp]\n"
        f"    {written_key}: [request_id]\n"
        "  - id: merge\n"
        "    op: merge_environments\n"
        "    left: esit\n"
        "    right: svp\n"
        "    keys: [request_id]\n",
        encoding="utf-8",
    )

    repaired = load_config(path)
    assert next(s for s in repaired["steps"] if s["op"] == "deduplicate")["keys"] == ["request_id"]
    validate_config(repaired)


def test_missing_input_file_names_the_source(workspace):
    with pytest.raises(Exception, match="esit"):
        _run(workspace)
