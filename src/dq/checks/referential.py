"""Referential family: every value must exist in the parent table's key set."""

from __future__ import annotations

from dq.checkdefs import CheckDef
from dq.checks.schema import Outcome, missing_detail
from dq.types import BatchContext


def relationships(defn: CheckDef, ctx: BatchContext) -> Outcome:
    column = defn.params["column"]
    expected = f"every {column} exists in the parent table"
    if (why := missing_detail(ctx.batch_df, [column])) is not None:
        return "error", None, expected, why
    values = ctx.batch_df[column].dropna()
    orphans = int((~values.isin(list(ctx.parent_keys))).sum())
    if orphans:
        return "fail", float(orphans), expected, f"{orphans} {column} not in the parent table"
    return "pass", 0.0, expected, ""
