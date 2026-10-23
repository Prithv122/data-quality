"""Freshness family: how long ago the newest row landed."""

from __future__ import annotations

import datetime as dt

from dq.checkdefs import CheckDef
from dq.checks.schema import Outcome
from dq.types import BatchContext, MetricValue


def _utc(t: dt.datetime) -> dt.datetime:
    return t if t.tzinfo is not None else t.replace(tzinfo=dt.UTC)


def lag_hours(ctx: BatchContext) -> float | None:
    """Hours from the newest loaded row to check time; None when nothing was ever loaded."""
    if ctx.max_ingested_at is None:
        return None
    return (_utc(ctx.check_time) - _utc(ctx.max_ingested_at)).total_seconds() / 3600


def max_age(defn: CheckDef, ctx: BatchContext) -> Outcome:
    warn = float(defn.params["warn_after_hours"])
    error = float(defn.params["error_after_hours"])
    expected = f"<= {warn:g} h (warn), <= {error:g} h (error)"
    lag = lag_hours(ctx)
    if lag is None:
        return "error", None, expected, "nothing loaded"
    if lag > error:
        return "fail", lag, expected, f"newest row is {lag:.1f} h old"
    if lag > warn:
        return "warn", lag, expected, f"newest row is {lag:.1f} h old"
    return "pass", lag, expected, ""


def lag_metric(ctx: BatchContext) -> MetricValue:
    lag = lag_hours(ctx)
    if lag is None:
        return MetricValue(None, "error", "nothing loaded")
    return MetricValue(lag)
