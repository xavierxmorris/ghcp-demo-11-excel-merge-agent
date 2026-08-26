"""Header standardisation and type coercion - the layer that absorbs SQL churn."""

from __future__ import annotations

import pandas as pd
import pytest

from xlflow.schema import Schema, SchemaError, canon_key


def test_canon_key_folds_spelling_variants():
    assert canon_key("Request ID") == canon_key("REQUEST_ID") == canon_key("request  id") == "request_id"
    assert canon_key("Cost Centre") == "cost_centre"
    assert canon_key(None) == ""


def test_aliases_resolve_across_both_environments(workspace):
    schema = Schema.load(workspace / "config" / "schema.yaml")
    mapping, unknown, duplicates = schema.resolve(
        ["Request ID", "CUST_NAME", "REQ_STATUS", "AMT", "geo", "SUBMIT_DT"]
    )
    assert mapping["CUST_NAME"] == "customer_name"
    assert mapping["REQ_STATUS"] == "status"
    assert mapping["AMT"] == "amount"
    assert mapping["geo"] == "region"
    assert unknown == []
    assert duplicates == {}


def test_unknown_header_is_reported_not_dropped_silently(workspace):
    schema = Schema.load(workspace / "config" / "schema.yaml")
    df = pd.DataFrame({"Request ID": ["R1"], "Mystery Column": ["x"]})
    out, report = schema.apply(df, source="svp")

    assert "Mystery Column" not in out.columns
    assert report.unknown == ["Mystery Column"]
    assert report.ok is False
    assert "Mystery Column" in report.alias_patch()


def test_missing_required_field_is_flagged(workspace):
    schema = Schema.load(workspace / "config" / "schema.yaml")
    _, report = schema.apply(pd.DataFrame({"Status": ["APPROVED"]}), source="esit")
    assert "request_id" in report.missing_required


def test_two_headers_claiming_one_field_is_ambiguous(workspace):
    schema = Schema.load(workspace / "config" / "schema.yaml")
    df = pd.DataFrame({"Request ID": ["R1"], "REQUEST_ID": ["R1"]})
    _, report = schema.apply(df, source="esit")
    assert "request_id" in report.duplicated
    assert report.ok is False


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("12,500.00", 12500.00),
        ("$7,650.25", 7650.25),
        ("  980 ", 980.0),
        ("(50.00)", -50.00),  # accountancy-style negative
        ("", None),
        ("n/a", None),
    ],
)
def test_messy_amounts_are_parsed(workspace, raw, expected):
    schema = Schema.load(workspace / "config" / "schema.yaml")
    df = pd.DataFrame({"Request ID": ["R1"], "Total Amount": [raw]})
    out, report = schema.apply(df, source="esit")
    value = out["amount"].iloc[0]

    if expected is None:
        assert pd.isna(value)
    else:
        assert float(value) == pytest.approx(expected)
        assert report.coercion_failures.get("amount") is None


def test_unparseable_value_counts_as_coercion_failure(workspace):
    schema = Schema.load(workspace / "config" / "schema.yaml")
    df = pd.DataFrame({"Request ID": ["R1"], "Total Amount": ["not a number"]})
    _, report = schema.apply(df, source="esit")
    assert report.coercion_failures["amount"] == 1


def test_case_normalisation_prevents_false_mismatches(workspace):
    """g.ROSSI@contoso.com and g.rossi@contoso.com are the same person."""
    schema = Schema.load(workspace / "config" / "schema.yaml")
    df = pd.DataFrame({"Request ID": [" req-1 "], "Owner Email": ["G.ROSSI@Contoso.com"]})
    out, _ = schema.apply(df, source="esit")
    assert out["request_id"].iloc[0] == "REQ-1"
    assert out["owner_email"].iloc[0] == "g.rossi@contoso.com"


def test_duplicate_alias_across_fields_is_rejected(tmp_path):
    (tmp_path / "bad.yaml").write_text(
        "version: 1\nkey: [a]\nfields:\n"
        "  a: {type: string, aliases: ['Shared']}\n"
        "  b: {type: string, aliases: ['Shared']}\n",
        encoding="utf-8",
    )
    with pytest.raises(SchemaError, match="claimed by both"):
        Schema.load(tmp_path / "bad.yaml")
