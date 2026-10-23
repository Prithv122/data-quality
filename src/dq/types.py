"""Types every module shares. Frozen with the pre-registration."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Literal

import pandas as pd

Status = Literal["pass", "warn", "fail", "error", "skipped"]
Family = Literal["schema", "freshness", "volume", "nulls", "unique", "range", "referential"]
FAMILIES: tuple[Family, ...] = (
    "schema",
    "freshness",
    "volume",
    "nulls",
    "unique",
    "range",
    "referential",
)

# An alarm is anything a team would be paged or warned about. A check that crashes
# (a column it needs is gone) is an alarm too: in production the run goes red.
ALARM_STATUSES: frozenset[str] = frozenset({"warn", "fail", "error"})


@dataclass(frozen=True)
class CheckResult:
    """One check evaluated once. The common output of every engine and source."""

    check: str  # check id from checks.yml, e.g. "null_rate.temp_max_c"
    family: Family
    table: str  # "raw.weather_daily", "lake.events", ...
    batch: int | None  # 1-based stream batch, None outside a stream
    status: Status
    observed: float | None  # the measured value (rows, rate, hours, violating rows)
    expected: str  # the rule as text, e.g. "<= 0.031 (median 0.020 + 4.0 x 0.0028)"
    detail: str = ""
    snapshot_id: int | None = None  # Iceberg snapshot the result describes (lake source)

    @property
    def alarm(self) -> bool:
        return self.status in ALARM_STATUSES


@dataclass(frozen=True)
class MetricValue:
    """A scalar a threshold check watches (row count, null rate, lag in hours)."""

    value: float | None  # None unless status == "ok"
    status: Literal["ok", "error", "skipped"] = "ok"
    detail: str = ""


@dataclass(frozen=True)
class BatchContext:
    """Everything a check may look at when one batch lands.

    `batch_df` holds exactly the rows that arrived in this batch, as delivered: it may be
    empty, may lack columns, may carry the wrong dtypes. Checks must not assume the contract
    holds; they report what they see.
    """

    table: str
    batch: int | None
    batch_df: pd.DataFrame
    # (city_id, date) keys loaded by earlier batches, for the cross-batch uniqueness rule.
    loaded_keys: frozenset[tuple[str, dt.date]]
    # city_id values present in raw.cities when this batch lands.
    parent_keys: frozenset[str]
    # max(ingested_at) over everything loaded so far, this batch included; None if nothing.
    max_ingested_at: dt.datetime | None
    check_time: dt.datetime
    extra: dict[str, object] = field(default_factory=dict)
