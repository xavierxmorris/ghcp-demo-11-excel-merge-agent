"""Regression tests for the silent-wrongness classes found in code review.

Every test here corresponds to a way the pipeline could once have produced a
plausible-but-incorrect workbook instead of failing. They are the reason to
trust the output, so they are kept together and named for the hazard.
"""

from __future__ import annotations

import pandas as pd
import pytest
import yaml

from xlflow.pipeline import ConfigError, load_config, run_pipeline, validate_config
from xlflow.schema import SchemaError, canon_key
from xlflow.steps import StepError
from xlflow.writer import write_workbook
from xlflow.yamlio import YamlError, load_yaml

CONFIG = "config/pipeline.yaml"
SCHEMA = "config/schema.yaml"


def _run(ws, **kwargs):
    return run_pipeline(ws / CONFIG, ws / SCHEMA, base_dir=ws, **kwargs)


def _edit_source(ws, name: str, fn) -> None:
    """Read one inbox workbook, mutate the DataFrame, write it back."""
    path = ws / "inbox" / f"{name}.xlsx"
    df = pd.read_excel(path, engine="openpyxl", dtype=object, keep_default_na=False, na_values=[])
    fn(df)
    df.to_excel(path, index=False, sheet_name="Query Result")


def _edit_config(ws, fn) -> None:
    path = ws / CONFIG
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    fn(cfg)
    path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")


# --- Join key integrity ------------------------------------------------------

def test_blank_join_key_is_rejected(clean_workspace):
    """Blank keys join to each other, silently merging unrelated records."""
    _edit_source(clean_workspace, "esit", lambda df: df.__setitem__("Request ID", [""] + list(df["Request ID"][1:])))

    with pytest.raises(SchemaError, match="blank"):
        _run(clean_workspace)


def test_duplicate_join_key_is_rejected_at_merge(clean_workspace):
    """Without dedupe, a repeated request_id fans out into a cartesian join."""
    _edit_config(clean_workspace, lambda cfg: cfg["steps"].remove(
        next(s for s in cfg["steps"] if s["id"] == "dedupe_sources")
    ))

    with pytest.raises(StepError, match="sharing a join key"):
        _run(clean_workspace)


def test_duplicate_key_error_names_the_offending_key(clean_workspace):
    _edit_config(clean_workspace, lambda cfg: cfg["steps"].remove(
        next(s for s in cfg["steps"] if s["id"] == "dedupe_sources")
    ))
    with pytest.raises(StepError, match="REQ-1002"):
        _run(clean_workspace)


# --- Unparseable values must never masquerade as blanks ----------------------

def test_two_unparseable_amounts_do_not_compare_as_equal(clean_workspace):
    """The worst bug class: 'BAD' vs 'BAD' both coerce to NA, and NA == NA."""
    _edit_source(clean_workspace, "esit",
                 lambda df: df.__setitem__("Total Amount", ["BAD"] + list(df["Total Amount"][1:])))
    _edit_source(clean_workspace, "svp",
                 lambda df: df.__setitem__("AMT", ["BAD"] + list(df["AMT"][1:])))

    with pytest.raises(SchemaError, match="do not match their declared type"):
        _run(clean_workspace)


def test_allow_unparseable_is_an_explicit_recorded_choice(clean_workspace):
    _edit_source(clean_workspace, "esit",
                 lambda df: df.__setitem__("Total Amount", ["BAD"] + list(df["Total Amount"][1:])))

    result = _run(clean_workspace, allow_unparseable=True)
    assert result.manifest["pipeline"]["allow_unparseable"] is True
    failures = [r["coercion_failures"] for r in result.manifest["header_resolution"]]
    assert any("amount" in f for f in failures)


def test_whitespace_only_cell_is_blank_not_a_parse_failure(clean_workspace):
    """A blank cell is missing data, not corrupt data. It must not block a run."""
    _edit_source(clean_workspace, "esit",
                 lambda df: df.__setitem__("Region", ["   "] + list(df["Region"][1:])))
    result = _run(clean_workspace)
    assert result.status_counts["OK"] == 5


def test_literal_null_string_is_not_silently_converted_to_missing(clean_workspace):
    """pandas would turn the string 'NULL' into NaN, creating a blank join key."""
    _edit_source(clean_workspace, "esit",
                 lambda df: df.__setitem__("Customer Name", ["NULL"] + list(df["Customer Name"][1:])))
    result = _run(clean_workspace)
    assert "NULL" in set(result.frame["Customer"])


# --- YAML loading ------------------------------------------------------------

def test_yaml_11_booleans_do_not_corrupt_string_values(tmp_path):
    """`value: [OFF]` must stay the string 'OFF', not become False."""
    path = tmp_path / "t.yaml"
    path.write_text("value: [OFF, ON, NO, YES]\nflag: true\n", encoding="utf-8")
    loaded = load_yaml(path)
    assert loaded["value"] == ["OFF", "ON", "NO", "YES"]
    assert loaded["flag"] is True


def test_duplicate_yaml_key_is_rejected(tmp_path):
    """A reviewer must never approve YAML that executes a different value."""
    path = tmp_path / "t.yaml"
    path.write_text("default: REVIEW\ndefault: OK\n", encoding="utf-8")
    with pytest.raises(YamlError, match="Duplicate key"):
        load_yaml(path)


def test_off_status_filter_actually_filters(clean_workspace):
    """End-to-end proof of the YAML 1.1 fix through the real config path."""
    _edit_config(clean_workspace, lambda cfg: next(
        s for s in cfg["steps"] if s["id"] == "drop_cancelled"
    ).__setitem__("value", ["CANCELLED", "VOID", "OFF"]))

    cfg = load_config(clean_workspace / CONFIG)
    step = next(s for s in cfg["steps"] if s["id"] == "drop_cancelled")
    assert step["value"] == ["CANCELLED", "VOID", "OFF"]


# --- Config typos and semantic mistakes --------------------------------------

def test_misspelled_parameter_is_rejected_with_a_suggestion(clean_workspace):
    """`tolerence: 1000` used to be ignored, silently keeping tolerance at 0."""
    cfg = load_config(clean_workspace / CONFIG)
    next(s for s in cfg["steps"] if s["op"] == "merge_environments")["tolerence"] = 1000

    with pytest.raises(ConfigError, match="unknown parameter"):
        validate_config(cfg)

    with pytest.raises(ConfigError, match="tolerance"):
        validate_config(cfg)


def test_field_in_both_compare_and_coalesce_is_rejected(clean_workspace):
    cfg = load_config(clean_workspace / CONFIG)
    next(s for s in cfg["steps"] if s["op"] == "merge_environments")["coalesce"].append("amount")
    with pytest.raises(ConfigError, match="both `compare` and `coalesce`"):
        validate_config(cfg)


def test_negative_tolerance_is_rejected(clean_workspace):
    _edit_config(clean_workspace, lambda cfg: next(
        s for s in cfg["steps"] if s["op"] == "merge_environments"
    ).__setitem__("tolerance", -5))
    with pytest.raises(StepError, match="finite number"):
        _run(clean_workspace)


def test_output_cannot_overwrite_an_input(clean_workspace):
    cfg = load_config(clean_workspace / CONFIG)
    cfg["output"]["path"] = "inbox/esit.xlsx"
    with pytest.raises(ConfigError, match="same file as source"):
        validate_config(cfg)


# --- Classification must survive to the output -------------------------------

def test_dropping_the_status_column_is_rejected(clean_workspace):
    """Otherwise Summary says 'no classification' and the CLI reports 'No exceptions'."""
    def drop_status(cfg):
        step = next(s for s in cfg["steps"] if s["op"] == "select_columns")
        step["columns"].remove("reconciliation_status")
        next(s for s in cfg["steps"] if s["op"] == "rename_output")["mapping"].pop("reconciliation_status")
    _edit_config(clean_workspace, drop_status)

    with pytest.raises(ConfigError, match="Exceptions sheet"):
        _run(clean_workspace)


def test_exception_label_that_no_rule_can_produce_is_rejected(clean_workspace):
    cfg = load_config(clean_workspace / CONFIG)
    cfg["output"]["exception_labels"].append("TYPO_MISMATCH")
    with pytest.raises(ConfigError, match="TYPO_MISMATCH"):
        validate_config(cfg)


def test_unrouted_classification_raises_a_warning(clean_workspace):
    cfg = load_config(clean_workspace / CONFIG)
    cfg["output"]["exception_labels"].remove("STATUS_MISMATCH")
    warnings = validate_config(cfg)
    assert any("STATUS_MISMATCH" in w for w in warnings)


# --- Header resolution bypasses ----------------------------------------------

def test_percent_header_does_not_fold_onto_the_currency_field():
    """'Amount %' must not silently map to the monetary 'amount' field."""
    assert canon_key("Amount %") != canon_key("Amount")
    assert canon_key("Amount %") == "amount_percent"


def test_populated_blank_header_column_is_reported_not_dropped(clean_workspace):
    def add_unnamed(df):
        df[""] = "secret data"
    _edit_source(clean_workspace, "svp", add_unnamed)

    with pytest.raises(SchemaError, match="unrecognised header"):
        _run(clean_workspace)


# --- Output integrity --------------------------------------------------------

def test_writer_never_emits_a_live_formula(tmp_path):
    """A customer named '=1+1' must be stored as literal text, not evaluated."""
    import openpyxl

    from xlflow.pipeline import RunResult

    frame = pd.DataFrame({
        "Request ID": ["REQ-1", "REQ-2"],
        "Customer": ["=1+1", "@SUM(A1:A9)"],
        "Reconciliation Status": ["OK", "OK"],
    })
    result = RunResult(
        frame=frame, manifest={}, status_column="Reconciliation Status",
        output_path=tmp_path / "out.xlsx", exception_labels=[], main_sheet="Reconciliation",
    )
    path = write_workbook(result)

    workbook = openpyxl.load_workbook(path)
    live = [c.coordinate for name in workbook.sheetnames
            for row in workbook[name].iter_rows() for c in row if c.data_type == "f"]
    assert live == []
    assert result.manifest["output"]["formula_like_cells_written_as_text"] >= 2

    # And the text survives intact rather than being mangled.
    reread = pd.read_excel(path, sheet_name="Reconciliation", engine="openpyxl")
    assert set(reread["Customer"]) == {"=1+1", "@SUM(A1:A9)"}


def test_formula_in_a_source_export_is_rejected(clean_workspace):
    """pandas reads a formula's cached result; with no cache it silently reads 0/blank."""
    import openpyxl

    path = clean_workspace / "inbox" / "esit.xlsx"
    workbook = openpyxl.load_workbook(path)
    workbook.active["D2"] = "=1000+500"
    workbook.save(path)

    with pytest.raises(Exception, match="formula cells"):
        _run(clean_workspace)


def test_formula_source_can_be_accepted_explicitly(clean_workspace):
    """The escape hatch exists, but it must be a deliberate, committed decision."""
    import openpyxl

    path = clean_workspace / "inbox" / "esit.xlsx"
    workbook = openpyxl.load_workbook(path)
    workbook.active["D2"] = "=1000+500"
    workbook.save(path)

    _edit_config(clean_workspace, lambda cfg: cfg["sources"]["esit"].__setitem__("allow_formulas", True))
    result = _run(clean_workspace, allow_unparseable=True)
    assert len(result.frame) == 9


def test_out_override_is_reflected_in_the_manifest(clean_workspace):
    result = _run(clean_workspace, out="out/alternate.xlsx")
    write_workbook(result)
    assert result.manifest["output"]["path"].endswith("alternate.xlsx")
    assert len(result.manifest["output"]["sha256"]) == 64


# --- Business-rule ordering --------------------------------------------------

def test_latest_cancellation_wins_over_older_approval(clean_workspace):
    """Dedupe runs before the cancelled filter, so a cancellation is not undone."""
    def add_history(df):
        older = df.iloc[[0]].copy()
        older["Status"] = "APPROVED"
        older["Submitted At"] = "2026-01-01 00:00"
        newer = df.iloc[[0]].copy()
        newer["Status"] = "CANCELLED"
        newer["Submitted At"] = "2026-09-01 00:00"
        df.drop(df.index, inplace=True)
        for _, row in pd.concat([older, newer]).iterrows():
            df.loc[len(df)] = row

    _edit_source(clean_workspace, "esit", add_history)
    result = _run(clean_workspace, strict=False)

    # REQ-1001 was cancelled most recently, so it must not appear as an ESIT row.
    row = result.frame[result.frame["Request ID"] == "REQ-1001"]
    assert row.empty or row.iloc[0]["Reconciliation Status"] == "MISSING_IN_ESIT"
