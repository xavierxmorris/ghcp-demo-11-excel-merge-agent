"""The op library.

Every transformation the agent is allowed to express lives here. Ops are
declarative and typed: the agent edits YAML, this module executes it. There is
deliberately no `eval` of user strings anywhere in this file, so a bad prompt
cannot turn into arbitrary code execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd


class StepError(ValueError):
    pass


@dataclass
class StepLog:
    step_id: str
    op: str
    detail: str = ""
    rows_before: int | None = None
    rows_after: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "op": self.op,
            "rows_before": self.rows_before,
            "rows_after": self.rows_after,
            "detail": self.detail,
        }


@dataclass
class PipelineState:
    frames: dict[str, pd.DataFrame]
    merged: pd.DataFrame | None = None
    ranks: dict[str, dict[Any, int]] = field(default_factory=dict)
    logs: list[StepLog] = field(default_factory=list)
    output_columns: list[str] = field(default_factory=list)
    # Name of the classification column, tracked through select/rename so the
    # Summary and Exceptions sheets keep working after headers are prettified.
    status_column: str | None = None

    @property
    def active(self) -> pd.DataFrame:
        if self.merged is None:
            raise StepError("This step runs after the merge, but no merge_environments step has run yet.")
        return self.merged


# --------------------------------------------------------------------------
# Predicates
# --------------------------------------------------------------------------

def _as_list(value: Any) -> list[Any]:
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def evaluate_predicate(df: pd.DataFrame, cond: dict[str, Any]) -> pd.Series:
    """Build a boolean mask from a structured condition (never a string eval)."""
    column = cond.get("column")
    operator = cond.get("operator", "eq")
    value = cond.get("value")

    if column is None:
        raise StepError(f"Condition is missing `column`: {cond!r}")
    if column not in df.columns:
        raise StepError(
            f"Condition references unknown column {column!r}. Available: {sorted(map(str, df.columns))}"
        )

    series = df[column]

    if operator == "is_null":
        return series.isna()
    if operator == "not_null":
        return series.notna()

    if operator in {"eq", "ne"}:
        if isinstance(value, bool) or value is None:
            # Booleans arrive as pandas 'boolean' dtype with possible <NA>;
            # compare via fillna so <NA> never silently becomes a match.
            filled = series.astype("object").where(series.notna(), None)
            mask = filled.map(lambda v: v is value if value is None else (v is True) == value if isinstance(v, bool) else v == value)
            mask = mask.fillna(False).astype(bool)
        else:
            mask = (series == value).fillna(False).astype(bool)
        return ~mask if operator == "ne" else mask

    if operator in {"gt", "gte", "lt", "lte"}:
        numeric = pd.to_numeric(series, errors="coerce")
        ops = {
            "gt": numeric > value,
            "gte": numeric >= value,
            "lt": numeric < value,
            "lte": numeric <= value,
        }
        return ops[operator].fillna(False).astype(bool)

    if operator in {"in", "not_in"}:
        allowed = _as_list(value)
        mask = series.isin(allowed).fillna(False).astype(bool)
        return ~mask if operator == "not_in" else mask

    if operator in {"contains", "starts_with", "ends_with"}:
        text = series.astype("object").map(lambda v: "" if v is None or pd.isna(v) else str(v))
        needle = str(value)
        if operator == "contains":
            return text.str.contains(needle, case=False, regex=False, na=False).astype(bool)
        if operator == "starts_with":
            return text.str.lower().str.startswith(needle.lower(), na=False).astype(bool)
        return text.str.lower().str.endswith(needle.lower(), na=False).astype(bool)

    raise StepError(
        f"Unknown operator {operator!r}. Valid operators: eq, ne, gt, gte, lt, lte, in, not_in, "
        "contains, starts_with, ends_with, is_null, not_null."
    )


def _values_match(left: Any, right: Any, tolerance: float) -> bool:
    """Two values agree if both are blank, or both are equal (numeric-aware)."""
    left_null = left is None or (not isinstance(left, str) and pd.isna(left))
    right_null = right is None or (not isinstance(right, str) and pd.isna(right))
    if left_null and right_null:
        return True
    if left_null or right_null:
        return False
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return abs(float(left) - float(right)) <= tolerance
    return str(left) == str(right)


# --------------------------------------------------------------------------
# Ops
# --------------------------------------------------------------------------

def _targets(state: PipelineState, step: dict[str, Any]) -> list[str]:
    """Which frames does this step act on: named sources, or the merged frame?"""
    applies_to = step.get("applies_to")
    if applies_to:
        missing = [s for s in applies_to if s not in state.frames]
        if missing:
            raise StepError(f"`applies_to` names unknown source(s) {missing}. Known: {sorted(state.frames)}")
        return list(applies_to)
    return []


def op_filter_rows(state: PipelineState, step: dict[str, Any]) -> StepLog:
    cond = {"column": step.get("column"), "operator": step.get("operator", "eq"), "value": step.get("value")}
    names = _targets(state, step)
    removed_total, before_total = 0, 0

    if names:
        for name in names:
            df = state.frames[name]
            before = len(df)
            mask = evaluate_predicate(df, cond)
            state.frames[name] = df.loc[mask].reset_index(drop=True)
            removed_total += before - len(state.frames[name])
            before_total += before
        after_total = before_total - removed_total
    else:
        df = state.active
        before_total = len(df)
        mask = evaluate_predicate(df, cond)
        state.merged = df.loc[mask].reset_index(drop=True)
        after_total = len(state.merged)
        removed_total = before_total - after_total

    scope = ", ".join(names) if names else "merged"
    return StepLog(
        step_id=step["id"],
        op="filter_rows",
        rows_before=before_total,
        rows_after=after_total,
        detail=f"{scope}: {cond['column']} {cond['operator']} {cond['value']!r} removed {removed_total} row(s)",
    )


def op_deduplicate(state: PipelineState, step: dict[str, Any]) -> StepLog:
    on = list(step.get("keys") or [])
    if not on:
        raise StepError(f"Step {step['id']!r}: `keys` must list at least one column.")
    keep = step.get("keep", "last")
    if keep not in {"first", "last"}:
        raise StepError(f"Step {step['id']!r}: `keep` must be 'first' or 'last', got {keep!r}.")
    order_by = step.get("order_by")

    names = _targets(state, step) or [None]
    before_total, after_total, details = 0, 0, []

    for name in names:
        df = state.frames[name] if name else state.active
        missing = [c for c in on if c not in df.columns]
        if missing:
            raise StepError(f"Step {step['id']!r}: dedupe columns {missing} not present in {name or 'merged'}.")
        before = len(df)
        work = df
        if order_by:
            if order_by not in df.columns:
                raise StepError(f"Step {step['id']!r}: `order_by` column {order_by!r} not present in {name or 'merged'}.")
            # kind='stable' keeps input order for ties, so runs stay reproducible.
            work = df.sort_values(order_by, kind="stable", na_position="first")
        deduped = work.drop_duplicates(subset=on, keep=keep).sort_index().reset_index(drop=True)
        if name:
            state.frames[name] = deduped
        else:
            state.merged = deduped
        before_total += before
        after_total += len(deduped)
        if before != len(deduped):
            details.append(f"{name or 'merged'}: dropped {before - len(deduped)}")

    return StepLog(
        step_id=step["id"],
        op="deduplicate",
        rows_before=before_total,
        rows_after=after_total,
        detail="; ".join(details) if details else "no duplicates found",
    )


def _assert_join_keys_usable(step_id: str, source: str, df: pd.DataFrame, keys: list[str]) -> None:
    """A join is only trustworthy if its keys are present and unique.

    Two failure modes this closes, both of which produce a plausible workbook:

    * Blank keys. pandas happily joins null to null, so two unrelated records
      with no ID become one row and can be classified OK.
    * Duplicate keys. An outer join fans out to the cartesian product of the
      matching rows, inflating counts and inventing mismatches.
    """
    from .schema import is_blank

    blank_mask = pd.Series(False, index=df.index)
    for key in keys:
        blank_mask = blank_mask | df[key].map(is_blank)
    blank_count = int(blank_mask.sum())
    if blank_count:
        rows = [int(i) + 2 for i in df.index[blank_mask][:5]]
        raise StepError(
            f"Step {step_id!r}: source {source!r} has {blank_count} row(s) with a blank join key "
            f"({keys}); first affected spreadsheet row(s): {rows}. Blank keys join to each other, "
            "which silently merges unrelated records. Fix the export or filter these rows out "
            "before the merge."
        )

    dupes = df.duplicated(subset=keys, keep=False)
    dupe_count = int(dupes.sum())
    if dupe_count:
        samples = df.loc[dupes, keys].drop_duplicates().head(5)
        listed = ", ".join(" / ".join(str(v) for v in row) for row in samples.itertuples(index=False))
        raise StepError(
            f"Step {step_id!r}: source {source!r} has {dupe_count} row(s) sharing a join key "
            f"({keys}); e.g. {listed}. Joining on a non-unique key multiplies rows. "
            "Add or fix a `deduplicate` step before the merge."
        )


def op_merge_environments(state: PipelineState, step: dict[str, Any]) -> StepLog:
    left_name, right_name = step.get("left"), step.get("right")
    for role, name in (("left", left_name), ("right", right_name)):
        if name not in state.frames:
            raise StepError(f"Step {step['id']!r}: `{role}` source {name!r} not found. Known: {sorted(state.frames)}")

    on = list(step.get("keys") or [])
    how = step.get("how", "outer")
    if how not in {"outer", "inner", "left", "right"}:
        raise StepError(f"Step {step['id']!r}: `how` must be outer/inner/left/right, got {how!r}.")
    compare = list(step.get("compare") or [])
    coalesce = list(step.get("coalesce") or [])

    overlap = sorted(set(compare) & set(coalesce))
    if overlap:
        raise StepError(
            f"Step {step['id']!r}: field(s) {overlap} appear in both `compare` and `coalesce`. "
            "Coalescing deletes the per-environment values, destroying the evidence behind the "
            "comparison. Choose one."
        )

    tolerance = float(step.get("tolerance", 0.0))
    if tolerance < 0 or tolerance != tolerance or tolerance == float("inf"):
        raise StepError(f"Step {step['id']!r}: `tolerance` must be a finite number >= 0, got {step.get('tolerance')!r}.")

    left = state.frames[left_name].copy()
    right = state.frames[right_name].copy()

    for name, df in ((left_name, left), (right_name, right)):
        missing = [c for c in on if c not in df.columns]
        if missing:
            raise StepError(f"Step {step['id']!r}: join key(s) {missing} missing from source {name!r}.")
        _assert_join_keys_usable(step["id"], name, df, on)

    left["_in_" + left_name] = True
    right["_in_" + right_name] = True

    merged = left.merge(right, on=on, how=how, suffixes=(f"_{left_name}", f"_{right_name}"))
    merged["_in_" + left_name] = merged["_in_" + left_name].fillna(False).astype(bool)
    merged["_in_" + right_name] = merged["_in_" + right_name].fillna(False).astype(bool)

    match_cols: list[str] = []
    for col in compare:
        lcol, rcol = f"{col}_{left_name}", f"{col}_{right_name}"
        if lcol not in merged.columns or rcol not in merged.columns:
            raise StepError(
                f"Step {step['id']!r}: cannot compare {col!r} because it is not present in both sources "
                f"(looked for {lcol!r} and {rcol!r})."
            )
        mcol = f"{col}_match"
        merged[mcol] = [
            _values_match(a, b, tolerance)
            for a, b in zip(merged[lcol].tolist(), merged[rcol].tolist())
        ]
        match_cols.append(mcol)

    for col in coalesce:
        lcol, rcol = f"{col}_{left_name}", f"{col}_{right_name}"
        if lcol in merged.columns and rcol in merged.columns:
            merged[col] = merged[lcol].where(merged[lcol].notna(), merged[rcol])
            merged = merged.drop(columns=[lcol, rcol])
        elif col not in merged.columns:
            raise StepError(f"Step {step['id']!r}: cannot coalesce {col!r}; it exists in neither source.")

    both = merged["_in_" + left_name] & merged["_in_" + right_name]
    if match_cols:
        all_match = merged[match_cols].all(axis=1) & both
    else:
        all_match = both
    merged["_all_match"] = all_match.astype(bool)

    state.merged = merged.reset_index(drop=True)
    state.frames = {}  # post-merge steps must not reach back into source frames

    only_left = int((~merged["_in_" + right_name]).sum())
    only_right = int((~merged["_in_" + left_name]).sum())
    return StepLog(
        step_id=step["id"],
        op="merge_environments",
        rows_before=len(left) + len(right),
        rows_after=len(merged),
        detail=(
            f"{how} join on {on}; {int(both.sum())} in both, "
            f"{only_left} only in {left_name}, {only_right} only in {right_name}; "
            f"compared {compare or 'nothing'}"
        ),
    )


_ARITH: dict[str, Callable[[pd.Series, pd.Series], pd.Series]] = {
    "subtract": lambda a, b: a - b,
    "add": lambda a, b: a + b,
    "multiply": lambda a, b: a * b,
    "divide": lambda a, b: a / b.replace(0, pd.NA),
    "abs_diff": lambda a, b: (a - b).abs(),
    "pct_diff": lambda a, b: ((a - b) / b.replace(0, pd.NA)) * 100,
}


def op_compute(state: PipelineState, step: dict[str, Any]) -> StepLog:
    df = state.active
    target = step.get("target")
    operator = step.get("operator")
    if operator not in _ARITH:
        raise StepError(f"Step {step['id']!r}: `operator` must be one of {sorted(_ARITH)}, got {operator!r}.")
    for role in ("left", "right"):
        if step.get(role) not in df.columns:
            raise StepError(f"Step {step['id']!r}: `{role}` column {step.get(role)!r} not found after merge.")

    a = pd.to_numeric(df[step["left"]], errors="coerce").astype("Float64")
    b = pd.to_numeric(df[step["right"]], errors="coerce").astype("Float64")
    result = _ARITH[operator](a, b)
    if step.get("decimals") is not None:
        result = result.round(int(step["decimals"]))
    df[target] = result
    state.merged = df

    populated = int(result.notna().sum())
    return StepLog(
        step_id=step["id"],
        op="compute",
        rows_before=len(df),
        rows_after=len(df),
        detail=f"{target} = {step['left']} {operator} {step['right']} ({populated} non-blank)",
    )


def op_derive_status(state: PipelineState, step: dict[str, Any]) -> StepLog:
    df = state.active
    target = step.get("target")
    if not target:
        raise StepError(f"Step {step['id']!r}: `target` is required.")
    rules = step.get("rules") or []
    if not rules:
        raise StepError(f"Step {step['id']!r}: at least one rule is required.")

    default = step.get("default", "REVIEW")
    result = pd.Series([default] * len(df), index=df.index, dtype="object")
    unassigned = pd.Series(True, index=df.index)
    counts: dict[str, int] = {}

    for rule in rules:
        label = rule.get("label")
        if label is None:
            raise StepError(f"Step {step['id']!r}: every rule needs a `label`.")
        conditions = rule.get("all") or rule.get("any")
        if not conditions:
            raise StepError(f"Step {step['id']!r}: rule {label!r} needs `all` or `any` conditions.")

        masks = [evaluate_predicate(df, c) for c in conditions]
        combined = masks[0]
        for m in masks[1:]:
            combined = (combined & m) if rule.get("all") else (combined | m)

        # First matching rule wins, so rule order in YAML is the priority order.
        hit = combined & unassigned
        result = result.where(~hit, label)
        unassigned = unassigned & ~hit
        counts[label] = int(hit.sum())

    counts[default] = int(unassigned.sum())
    df[target] = result
    state.merged = df
    state.status_column = target

    breakdown = ", ".join(f"{k}={v}" for k, v in counts.items() if v)
    return StepLog(step_id=step["id"], op="derive_status", rows_before=len(df), rows_after=len(df), detail=breakdown or "no rows")


def op_map_values(state: PipelineState, step: dict[str, Any]) -> StepLog:
    df = state.active
    source, target = step.get("source"), step.get("target")
    mapping = step.get("mapping") or {}
    default = step.get("default")

    if source not in df.columns:
        raise StepError(f"Step {step['id']!r}: `source` column {source!r} not found.")
    if not mapping:
        raise StepError(f"Step {step['id']!r}: `mapping` must not be empty.")

    df[target] = df[source].map(lambda v: mapping.get(v, default))

    # Declaration order of the mapping VALUES defines priority, which downstream
    # `sort` steps can use as "<target>_rank". Documented in the agent contract.
    order: dict[Any, int] = {}
    for value in list(mapping.values()) + ([default] if default is not None else []):
        if value not in order:
            order[value] = len(order)
    state.ranks[target] = order
    state.merged = df

    return StepLog(
        step_id=step["id"],
        op="map_values",
        rows_before=len(df),
        rows_after=len(df),
        detail=f"{source} -> {target}; rank order {list(order)}",
    )


def op_add_constant(state: PipelineState, step: dict[str, Any]) -> StepLog:
    df = state.active
    df[step["target"]] = step.get("value")
    state.merged = df
    return StepLog(step_id=step["id"], op="add_constant", rows_before=len(df), rows_after=len(df),
                   detail=f"{step['target']} = {step.get('value')!r}")


def op_sort(state: PipelineState, step: dict[str, Any]) -> StepLog:
    df = state.active
    by = list(step.get("by") or [])
    if not by:
        raise StepError(f"Step {step['id']!r}: `by` must list at least one column.")
    ascending = step.get("ascending", True)
    if isinstance(ascending, list) and len(ascending) != len(by):
        raise StepError(f"Step {step['id']!r}: `ascending` list must be the same length as `by`.")

    materialised: list[str] = []
    for col in by:
        if col in df.columns:
            continue
        if col.endswith("_rank"):
            base = col[: -len("_rank")]
            order = state.ranks.get(base)
            if order is not None and base in df.columns:
                df[col] = df[base].map(lambda v: order.get(v, len(order)))
                materialised.append(col)
                continue
        raise StepError(
            f"Step {step['id']!r}: sort column {col!r} does not exist. "
            f"Available: {sorted(map(str, df.columns))}"
        )

    state.merged = df.sort_values(by=by, ascending=ascending, kind="stable").reset_index(drop=True)
    detail = f"sorted by {by}"
    if materialised:
        detail += f" (derived {materialised} from map_values order)"
    return StepLog(step_id=step["id"], op="sort", rows_before=len(df), rows_after=len(df), detail=detail)


def op_select_columns(state: PipelineState, step: dict[str, Any]) -> StepLog:
    df = state.active
    columns = list(step.get("columns") or [])
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise StepError(
            f"Step {step['id']!r}: cannot select missing column(s) {missing}. "
            f"Available: {sorted(map(str, df.columns))}"
        )
    state.merged = df.loc[:, columns].copy()
    state.output_columns = columns
    if state.status_column is not None and state.status_column not in columns:
        state.status_column = None
    return StepLog(step_id=step["id"], op="select_columns", rows_before=len(df), rows_after=len(df),
                   detail=f"kept {len(columns)} column(s)")


def op_rename_output(state: PipelineState, step: dict[str, Any]) -> StepLog:
    df = state.active
    mapping = step.get("mapping") or {}
    unknown = [k for k in mapping if k not in df.columns]
    if unknown:
        raise StepError(f"Step {step['id']!r}: rename_output references missing column(s) {unknown}.")
    state.merged = df.rename(columns=mapping)
    if state.status_column in mapping:
        state.status_column = mapping[state.status_column]
    state.output_columns = [mapping.get(c, c) for c in state.output_columns]
    return StepLog(step_id=step["id"], op="rename_output", rows_before=len(df), rows_after=len(df),
                   detail=f"renamed {len(mapping)} header(s)")


OPS: dict[str, Callable[[PipelineState, dict[str, Any]], StepLog]] = {
    "filter_rows": op_filter_rows,
    "deduplicate": op_deduplicate,
    "merge_environments": op_merge_environments,
    "compute": op_compute,
    "derive_status": op_derive_status,
    "map_values": op_map_values,
    "add_constant": op_add_constant,
    "sort": op_sort,
    "select_columns": op_select_columns,
    "rename_output": op_rename_output,
}
