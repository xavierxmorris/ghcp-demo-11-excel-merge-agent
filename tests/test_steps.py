"""Op-level behaviour: the rules the agent is allowed to compose."""

from __future__ import annotations

import pandas as pd
import pytest

from xlflow.steps import (
    PipelineState,
    StepError,
    evaluate_predicate,
    op_deduplicate,
    op_derive_status,
    op_map_values,
    op_merge_environments,
    op_sort,
)


def _state(**frames) -> PipelineState:
    return PipelineState(frames={k: v.copy() for k, v in frames.items()})


def test_predicate_rejects_unknown_column():
    df = pd.DataFrame({"status": ["OK"]})
    with pytest.raises(StepError, match="unknown column"):
        evaluate_predicate(df, {"column": "nope", "operator": "eq", "value": 1})


def test_predicate_rejects_unknown_operator():
    df = pd.DataFrame({"status": ["OK"]})
    with pytest.raises(StepError, match="Unknown operator"):
        evaluate_predicate(df, {"column": "status", "operator": "regex", "value": ".*"})


def test_not_in_keeps_nulls():
    """A blank status must not be silently dropped by `not_in`."""
    df = pd.DataFrame({"status": ["APPROVED", "CANCELLED", None]})
    mask = evaluate_predicate(df, {"column": "status", "operator": "not_in", "value": ["CANCELLED"]})
    assert list(mask) == [True, False, True]


def test_deduplicate_keeps_latest_by_order_by():
    df = pd.DataFrame(
        {
            "request_id": ["R1", "R1", "R2"],
            "amount": [100.0, 250.0, 50.0],
            "submitted_at": pd.to_datetime(["2026-07-01", "2026-07-06", "2026-07-02"]),
        }
    )
    state = _state(esit=df)
    op_deduplicate(state, {"id": "d", "op": "deduplicate", "applies_to": ["esit"], "keys": ["request_id"],
                           "order_by": "submitted_at", "keep": "last"})
    out = state.frames["esit"].set_index("request_id")
    assert out.loc["R1", "amount"] == 250.0
    assert len(out) == 2


def test_merge_match_semantics():
    """Both-blank agrees; one-sided blank does not."""
    left = pd.DataFrame({"request_id": ["R1", "R2", "R3"], "amount": [100.0, 100.0, None]})
    right = pd.DataFrame({"request_id": ["R1", "R2", "R3"], "amount": [100.0, 999.0, None]})
    state = _state(esit=left, svp=right)
    op_merge_environments(state, {"id": "m", "op": "merge_environments", "left": "esit", "right": "svp",
                                  "keys": ["request_id"], "how": "outer", "compare": ["amount"]})
    out = state.merged.set_index("request_id")
    assert bool(out.loc["R1", "amount_match"]) is True
    assert bool(out.loc["R2", "amount_match"]) is False
    assert bool(out.loc["R3", "amount_match"]) is True   # both blank
    assert bool(out.loc["R3", "_all_match"]) is True


def test_merge_outer_join_keeps_one_sided_rows():
    left = pd.DataFrame({"request_id": ["R1", "R9"], "status": ["A", "A"]})
    right = pd.DataFrame({"request_id": ["R1", "R10"], "status": ["A", "A"]})
    state = _state(esit=left, svp=right)
    op_merge_environments(state, {"id": "m", "op": "merge_environments", "left": "esit", "right": "svp",
                                  "keys": ["request_id"], "how": "outer", "compare": ["status"]})
    out = state.merged.set_index("request_id")
    assert set(out.index) == {"R1", "R9", "R10"}
    assert bool(out.loc["R9", "_in_svp"]) is False
    assert bool(out.loc["R10", "_in_esit"]) is False
    assert bool(out.loc["R9", "_all_match"]) is False


def test_merge_rejects_compare_field_missing_from_a_source():
    left = pd.DataFrame({"request_id": ["R1"], "amount": [1.0]})
    right = pd.DataFrame({"request_id": ["R1"]})
    state = _state(esit=left, svp=right)
    with pytest.raises(StepError, match="cannot compare"):
        op_merge_environments(state, {"id": "m", "op": "merge_environments", "left": "esit", "right": "svp",
                                      "keys": ["request_id"], "how": "outer", "compare": ["amount"]})


def test_derive_status_first_matching_rule_wins():
    df = pd.DataFrame({"_in_svp": [False, True], "amount_match": [False, False]})
    state = PipelineState(frames={}, merged=df)
    op_derive_status(state, {
        "id": "c", "op": "derive_status", "target": "cls", "default": "REVIEW",
        "rules": [
            {"label": "MISSING_IN_SVP", "all": [{"column": "_in_svp", "operator": "eq", "value": False}]},
            {"label": "AMOUNT_MISMATCH", "all": [{"column": "amount_match", "operator": "eq", "value": False}]},
        ],
    })
    # Row 0 satisfies both rules; the earlier one must win.
    assert list(state.merged["cls"]) == ["MISSING_IN_SVP", "AMOUNT_MISMATCH"]


def test_derive_status_falls_back_to_default():
    df = pd.DataFrame({"amount_match": [True]})
    state = PipelineState(frames={}, merged=df)
    op_derive_status(state, {"id": "c", "op": "derive_status", "target": "cls", "default": "REVIEW",
                             "rules": [{"label": "X", "all": [{"column": "amount_match", "operator": "eq", "value": False}]}]})
    assert list(state.merged["cls"]) == ["REVIEW"]


def test_sort_derives_rank_from_map_values_declaration_order():
    df = pd.DataFrame({"cls": ["OK", "AMOUNT_MISMATCH", "STATUS_MISMATCH"], "request_id": ["R3", "R1", "R2"]})
    state = PipelineState(frames={}, merged=df)
    op_map_values(state, {"id": "s", "op": "map_values", "source": "cls", "target": "severity", "default": "LOW",
                          "mapping": {"AMOUNT_MISMATCH": "HIGH", "STATUS_MISMATCH": "MEDIUM", "OK": "NONE"}})
    op_sort(state, {"id": "o", "op": "sort", "by": ["severity_rank", "request_id"], "ascending": [True, True]})
    assert list(state.merged["severity"]) == ["HIGH", "MEDIUM", "NONE"]


def test_sort_on_truly_missing_column_is_an_error():
    state = PipelineState(frames={}, merged=pd.DataFrame({"a": [1]}))
    with pytest.raises(StepError, match="does not exist"):
        op_sort(state, {"id": "o", "op": "sort", "by": ["ghost"]})
