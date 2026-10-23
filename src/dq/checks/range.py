"""Range family: accepted bounds on one column, and one column never below another."""

from __future__ import annotations

from dq.checkdefs import CheckDef
from dq.checks.schema import Outcome, missing_detail, numeric_column
from dq.types import BatchContext


def accepted_range(defn: CheckDef, ctx: BatchContext) -> Outcome:
    column, lo, hi = defn.params["column"], defn.params["min"], defn.params["max"]
    expected = f"within [{lo:g}, {hi:g}]"
    if (why := missing_detail(ctx.batch_df, [column])) is not None:
        return "error", None, expected, why
    values, why = numeric_column(ctx.batch_df, column)
    if values is None:
        return "error", None, expected, why
    bad = int(((values < lo) | (values > hi)).sum())  # NaN compares False: nulls pass
    if bad:
        return "fail", float(bad), expected, f"{bad} {column} outside the range"
    return "pass", 0.0, expected, ""


def column_pair_ge(defn: CheckDef, ctx: BatchContext) -> Outcome:
    a, b = defn.params["column_a"], defn.params["column_b"]
    expected = f"{a} >= {b}"
    if (why := missing_detail(ctx.batch_df, [a, b])) is not None:
        return "error", None, expected, why
    va, why_a = numeric_column(ctx.batch_df, a)
    vb, why_b = numeric_column(ctx.batch_df, b)
    if va is None or vb is None:
        return "error", None, expected, why_a or why_b
    bad = int((va < vb).sum())  # a NaN on either side compares False, as in SQL
    if bad:
        return "fail", float(bad), expected, f"{bad} rows with {a} < {b}"
    return "pass", 0.0, expected, ""
