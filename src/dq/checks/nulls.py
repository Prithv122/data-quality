"""Nulls family: not_null as a static rule, null_rate as a metric for the threshold arms."""

from __future__ import annotations

from dq.checkdefs import CheckDef
from dq.checks.schema import Outcome, missing_detail
from dq.types import BatchContext, MetricValue


def not_null(defn: CheckDef, ctx: BatchContext) -> Outcome:
    column = defn.params["column"]
    expected = "no nulls"
    if (why := missing_detail(ctx.batch_df, [column])) is not None:
        return "error", None, expected, why
    nulls = int(ctx.batch_df[column].isna().sum())
    if nulls:
        return "fail", float(nulls), expected, f"{nulls} null {column}"
    return "pass", 0.0, expected, ""


def null_rate(defn: CheckDef, ctx: BatchContext) -> MetricValue:
    column = defn.params["column"]
    df = ctx.batch_df
    if (why := missing_detail(df, [column])) is not None:
        return MetricValue(None, "error", why)
    if df.empty:
        return MetricValue(None, "skipped", "empty batch")
    return MetricValue(float(df[column].isna().sum()) / len(df))
