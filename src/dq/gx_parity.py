"""Gate G4: Great Expectations re-implements the static rules; verdicts must agree.

GX is imported only here and only when a function runs, so the rest of the package works
without the optional `gx` dependency group. One ephemeral context, one pandas data asset and
one whole-dataframe batch definition are built per process; each batch frame is passed as a
batch parameter, which keeps the per-batch cost to the validations themselves.

Rules GX cannot run on a single batch are out of scope and return nothing: `staging_sql_runs`
(a SQL build) and `max_age` (freshness needs the clock and the whole table).
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import pandas as pd

from dq.checkdefs import CheckDef, CheckSuite
from dq.types import BatchContext, CheckResult, Status

os.environ.setdefault("GX_ANALYTICS_ENABLED", "False")
logging.getLogger("great_expectations").setLevel(logging.ERROR)

SUPPORTED_RULES: frozenset[str] = frozenset(
    {
        "not_null",
        "unique_with_history",
        "accepted_range",
        "column_pair_ge",
        "relationships",
        "contract_equal",
    }
)

# Per logical type: (names handed to GX's type-list expectation, dtype names GX reports back).
# GX 1.24.1 on pandas 3 cannot match the default `str` dtype or a tz-aware timestamp column by
# any name in a type list (they compare against numpy types), but it does report their dtype
# names ("str", "Timestamp") in the result, so those are accepted from the report. A tz-naive
# datetime column is reported as "datetime64" and therefore does not count as timestamptz.
# `date` columns hold datetime.date objects (dtype object); GX type-checks object columns
# element by element, and the name "date" is the one that resolves to datetime.date.
_TYPE_NAMES: dict[str, tuple[list[str], frozenset[str]]] = {
    "double": (["float64", "Float64Dtype"], frozenset({"float64"})),
    "real": (["float32", "Float32Dtype"], frozenset({"float32"})),
    "text": (["str", "StringDtype"], frozenset({"str"})),
    "integer": (["int64", "int32", "Int64Dtype", "Int32Dtype"], frozenset({"int64", "int32"})),
    "numeric": (["float64", "Float64Dtype"], frozenset({"float64"})),
    "boolean": (["bool", "BooleanDtype"], frozenset({"bool", "bool_"})),
    "date": (["date"], frozenset()),
    "timestamptz": (["Timestamp"], frozenset({"Timestamp"})),
}


@dataclass
class _Outcome:
    success: bool
    errored: bool = False
    unexpected: float | None = None
    note: str = ""


@dataclass
class _Cost:
    batches: int = 0
    validations: int = 0
    seconds: float = 0.0


_COST = _Cost()


def gx_cost() -> dict[str, float]:
    """Wall-clock cost of GX since the last reset: batches, validations, seconds, s/batch."""
    per = _COST.seconds / _COST.batches if _COST.batches else 0.0
    return {
        "batches": _COST.batches,
        "validations": _COST.validations,
        "seconds": _COST.seconds,
        "seconds_per_batch": per,
    }


def reset_gx_cost() -> None:
    global _COST
    _COST = _Cost()


@lru_cache(maxsize=1)
def _env() -> Any:
    import great_expectations as gx
    from great_expectations.data_context.types.base import ProgressBarsConfig

    context = gx.get_context(mode="ephemeral")
    context.variables.progress_bars = ProgressBarsConfig(globally=False, metric_calculations=False)
    source = context.data_sources.add_pandas("dq_batches")
    asset = source.add_dataframe_asset("batch")
    return asset.add_batch_definition_whole_dataframe("whole")


def _gxe() -> Any:
    import great_expectations.expectations as gxe

    return gxe


def _raised(result: Any) -> bool:
    info = result.exception_info
    if not isinstance(info, dict):
        return False
    if info.get("raised_exception") is True:
        return True
    return any(isinstance(v, dict) and v.get("raised_exception") for v in info.values())


def _validate(df: pd.DataFrame, expectation: Any) -> _Outcome:
    batch = _env().get_batch(batch_parameters={"dataframe": df})
    t0 = time.perf_counter()
    result = batch.validate(expectation)
    _COST.seconds += time.perf_counter() - t0
    _COST.validations += 1
    body = result.result or {}
    unexpected = body.get("unexpected_count")
    if _raised(result):
        return _Outcome(False, errored=True, note="expectation raised (column missing?)")
    return _Outcome(bool(result.success), unexpected=unexpected)


def _col(df: pd.DataFrame, name: str) -> bool:
    return name in df.columns


# --- rules ------------------------------------------------------------------------------


def _not_null(defn: CheckDef, ctx: BatchContext, _c: dict[str, str]) -> list[_Outcome]:
    col = defn.params["column"]
    return [_validate(ctx.batch_df, _gxe().ExpectColumnValuesToNotBeNull(column=col))]


def _unique(defn: CheckDef, ctx: BatchContext, _c: dict[str, str]) -> list[_Outcome]:
    cols = list(defn.params["columns"])
    df = ctx.batch_df
    if not all(_col(df, c) for c in cols):
        # Let GX raise on the missing column so the verdict is its own.
        return [_validate(df, _gxe().ExpectCompoundColumnsToBeUnique(column_list=cols))]
    # Unique within the batch and against keys already loaded is uniqueness of the two
    # together. Only loaded keys on the batch's own dates can collide; narrowing to them keeps
    # the frame small. (A value-set expectation scales with the set size inside GX: about two
    # seconds per batch at 10,000 loaded keys.)
    dates = set(df[cols[1]].dropna())
    prior = [k for k in ctx.loaded_keys if k[1] in dates]
    prior_df = pd.DataFrame(prior, columns=cols)
    both = pd.concat([prior_df, df.loc[:, cols]], ignore_index=True)
    return [_validate(both, _gxe().ExpectCompoundColumnsToBeUnique(column_list=cols))]


def _range(defn: CheckDef, ctx: BatchContext, _c: dict[str, str]) -> list[_Outcome]:
    p = defn.params
    e = _gxe().ExpectColumnValuesToBeBetween(
        column=p["column"],
        min_value=p["min"],
        max_value=p["max"],
        strict_min=False,
        strict_max=False,
    )
    return [_validate(ctx.batch_df, e)]


def _pair(defn: CheckDef, ctx: BatchContext, _c: dict[str, str]) -> list[_Outcome]:
    p = defn.params
    # GX's default counts a row with one missing value as a failure; the rule passes it.
    e = _gxe().ExpectColumnPairValuesAToBeGreaterThanB(
        column_A=p["column_a"],
        column_B=p["column_b"],
        or_equal=True,
        ignore_row_if="either_value_is_missing",
    )
    return [_validate(ctx.batch_df, e)]


def _relationships(defn: CheckDef, ctx: BatchContext, _c: dict[str, str]) -> list[_Outcome]:
    e = _gxe().ExpectColumnValuesToBeInSet(
        column=defn.params["column"], value_set=sorted(ctx.parent_keys)
    )
    return [_validate(ctx.batch_df, e)]


def _contract(_d: CheckDef, ctx: BatchContext, contract: dict[str, str]) -> list[_Outcome]:
    df = ctx.batch_df
    gxe = _gxe()
    outs = [
        _validate(df, gxe.ExpectTableColumnsToMatchSet(column_set=list(contract), exact_match=True))
    ]
    for name, logical in contract.items():
        if not _col(df, name):
            continue  # the column-set expectation already failed on it
        names, reported = _TYPE_NAMES[logical]
        batch = _env().get_batch(batch_parameters={"dataframe": df})
        t0 = time.perf_counter()
        res = batch.validate(gxe.ExpectColumnValuesToBeInTypeList(column=name, type_list=names))
        _COST.seconds += time.perf_counter() - t0
        _COST.validations += 1
        if _raised(res):
            outs.append(_Outcome(False, errored=True, note=f"{name}: expectation raised"))
            continue
        seen = (res.result or {}).get("observed_value")
        ok = bool(res.success) or seen in reported
        outs.append(_Outcome(ok, note="" if ok else f"{name}: dtype {seen!r}, want {logical}"))
    return outs


_RULES: dict[str, Callable[[CheckDef, BatchContext, dict[str, str]], list[_Outcome]]] = {
    "not_null": _not_null,
    "unique_with_history": _unique,
    "accepted_range": _range,
    "column_pair_ge": _pair,
    "relationships": _relationships,
    "contract_equal": _contract,
}


def _expected_text(defn: CheckDef) -> str:
    p = defn.params
    match defn.rule:
        case "not_null":
            return f"no nulls in {p['column']}"
        case "unique_with_history":
            return f"({', '.join(p['columns'])}) unique in the batch and among loaded keys"
        case "accepted_range":
            return f"{p['column']} in [{p['min']}, {p['max']}], nulls pass"
        case "column_pair_ge":
            return f"{p['column_a']} >= {p['column_b']}, rows with a null pass"
        case "relationships":
            return f"{p['column']} in the parent table's keys, nulls pass"
        case _:
            return "columns and logical types equal the upstream contract"


def _status(outs: list[_Outcome]) -> Status:
    if any(o.errored for o in outs):
        return "error"
    return "pass" if all(o.success for o in outs) else "fail"


def gx_static_results(
    suite: CheckSuite, ctx: BatchContext, contract_columns: dict[str, str]
) -> list[CheckResult]:
    """Run every static check GX can express on one batch; one CheckResult per check."""
    results: list[CheckResult] = []
    for defn in suite.checks:
        if defn.kind != "static" or defn.rule not in SUPPORTED_RULES:
            continue
        outs = _RULES[defn.rule](defn, ctx, contract_columns)
        status = _status(outs)
        bad = sum(o.unexpected or 0 for o in outs)
        notes = "; ".join(o.note for o in outs if o.note)
        results.append(
            CheckResult(
                check=defn.id,
                family=defn.family,
                table=ctx.table,
                batch=ctx.batch,
                status=status,
                observed=None if status == "error" else float(bad),
                expected=_expected_text(defn),
                detail=f"great_expectations: {notes}" if notes else "great_expectations",
            )
        )
    _COST.batches += 1
    return results


def parity(own: list[CheckResult], gx: list[CheckResult]) -> list[str]:
    """Mismatches between own code and GX on (check, batch): alarm versus no alarm.

    Statuses may differ in kind (own `fail` against GX `error`) as long as both alarm, since
    the gate asks whether the rule fires, not how the engine words it.
    """
    index = {(r.check, r.batch): r for r in own}
    out: list[str] = []
    for g in gx:
        o = index.get((g.check, g.batch))
        where = f"{g.check} @ batch {g.batch}"
        if o is None:
            out.append(f"{where}: GX produced a result, own code did not")
        elif o.alarm != g.alarm:
            out.append(
                f"{where}: own {o.status} (observed {o.observed}) vs GX {g.status} "
                f"(observed {g.observed})"
            )
    return out
