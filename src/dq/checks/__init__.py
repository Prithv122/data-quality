"""The check rules named in `checks.yml`, one module per family.

Rules mirror dbt's semantics and are pre-registered. They never assume the contract holds: a
missing or non-numeric column is an `error` result, not an exception.
"""

from __future__ import annotations

from collections.abc import Callable

from dq import adaptive
from dq.checkdefs import CheckDef
from dq.checks import freshness, nulls, referential, schema, unique, volume
from dq.checks import range as range_checks
from dq.contract import Contract
from dq.types import BatchContext, CheckResult, MetricValue

_RULES: dict[str, Callable[[CheckDef, BatchContext], schema.Outcome]] = {
    "staging_sql_runs": schema.staging_sql_runs,
    "max_age": freshness.max_age,
    "not_null": nulls.not_null,
    "unique_with_history": unique.unique_with_history,
    "accepted_range": range_checks.accepted_range,
    "column_pair_ge": range_checks.column_pair_ge,
    "relationships": referential.relationships,
}
_CONTRACT_RULE = "contract_equal"

_METRICS: dict[str, Callable[[CheckDef, BatchContext], MetricValue]] = {
    "row_count": lambda defn, ctx: volume.row_count(ctx),
    "null_rate": nulls.null_rate,
    "lag_hours": lambda defn, ctx: freshness.lag_metric(ctx),
}


def run_static(defn: CheckDef, ctx: BatchContext, contract: Contract | None = None) -> CheckResult:
    if defn.kind != "static" or (defn.rule not in _RULES and defn.rule != _CONTRACT_RULE):
        raise ValueError(f"{defn.id}: not a static check with a known rule ({defn.rule!r})")
    if defn.rule == _CONTRACT_RULE:
        outcome = schema.contract_equal(defn, ctx, contract)
    else:
        outcome = _RULES[defn.rule](defn, ctx)
    status, observed, expected, detail = outcome
    return CheckResult(
        check=defn.id,
        family=defn.family,
        table=ctx.table,
        batch=ctx.batch,
        status=status,
        observed=observed,
        expected=expected,
        detail=detail,
    )


def compute_metric(defn: CheckDef, ctx: BatchContext) -> MetricValue:
    if defn.metric not in _METRICS:
        raise ValueError(f"{defn.id}: unknown metric {defn.metric!r}")
    return _METRICS[defn.metric](defn, ctx)


def threshold_result(
    defn: CheckDef, table: str, batch: int | None, decision: adaptive.Decision
) -> CheckResult:
    return CheckResult(
        check=defn.id,
        family=defn.family,
        table=table,
        batch=batch,
        status=decision.status,
        observed=decision.observed,
        expected=decision.expected,
        detail=decision.detail,
    )
