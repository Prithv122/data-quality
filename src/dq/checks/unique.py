"""Unique family: (city_id, date) must not repeat in the batch or against keys already loaded."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd

from dq.checkdefs import CheckDef
from dq.checks.schema import Outcome, missing_detail
from dq.types import BatchContext


def _day(v: Any) -> Any:
    return v.date() if isinstance(v, dt.datetime) else v


def unique_with_history(defn: CheckDef, ctx: BatchContext) -> Outcome:
    cols = list(defn.params["columns"])
    expected = f"no ({', '.join(cols)}) repeated in the batch or already loaded"
    df = ctx.batch_df
    if (why := missing_detail(df, cols)) is not None:
        return "error", None, expected, why

    seen: set[tuple] = set()
    repeats = loaded = offending = 0
    for raw_key in zip(*(df[c] for c in cols), strict=True):
        if any(pd.isna(part) for part in raw_key):
            continue  # a null key part is the not-null check's business
        key = tuple(_day(part) for part in raw_key)
        is_repeat = key in seen
        is_loaded = key in ctx.loaded_keys
        seen.add(key)
        repeats += is_repeat
        loaded += is_loaded
        offending += is_repeat or is_loaded  # a row that is both counts once
    if offending:
        return (
            "fail",
            float(offending),
            expected,
            f"{repeats} repeated in batch, {loaded} already loaded",
        )
    return "pass", 0.0, expected, ""
