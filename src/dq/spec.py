"""Everything the pre-registered experiment depends on. Frozen by PREREGISTRATION.md.

Changing anything here needs a dated amendment in PREREGISTRATION.md first.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Literal

from dq.types import Family

# --- upstream contracts ----------------------------------------------------------------

UPSTREAM_22_SHA = "e20cecc8e48a7672f267b5686807f1e96b698ca4"
UPSTREAM_35_SHA = "50bee72ec2722aa82717ff39c914ac4a4a4c9dc4"

# The #22 fixture mirrors #22's real data window exactly.
FIXTURE_START = dt.date(2023, 1, 1)
FIXTURE_END = dt.date(2024, 12, 31)  # inclusive: 731 days
FIXTURE_SEED = 22

# --- the simulated batch stream --------------------------------------------------------

Arm = Literal["S0", "S1", "A"]
ARMS: tuple[Arm, ...] = ("S0", "S1", "A")
World = Literal["W1", "W0"]
WORLDS: tuple[World, ...] = ("W1", "W0")  # W1 primary (network grows), W0 sensitivity

N_BATCHES = 60
WARMUP_BATCHES = 14  # batches 1..14: baseline only, never scored
EVAL_FIRST = WARMUP_BATCHES + 1  # batches 15..60 are scored
BASELINE_WINDOW = 14  # rolling arm: the last 14 accepted batches
MIN_BASELINE = 7  # fewer accepted batches than this -> threshold check is skipped
STREAM_START = dt.date(2024, 1, 1)  # data date of batch 1
INGEST_OFFSET = dt.timedelta(days=1, hours=2)  # batch for day d lands at d+1 02:00 UTC
CHECK_OFFSET = dt.timedelta(days=1, hours=6)  # checks run at d+1 06:00 UTC

STATIONS_PER_CITY = 18  # 10 seed cities x 18 = 180 stations at batch 1
ONBOARD_EVERY = 4  # W1 only: one new station every 4 batches (round-robin over cities)
P_OUTAGE = 0.02  # a station-day row never arrives
P_LATE = 0.03  # a station-day row arrives one batch late
BASE_NULL_RATE = {
    "temp_max_c": 0.005,
    "temp_min_c": 0.005,
    "precip_mm": 0.02,
    "wind_max_kmh": 0.01,
}
TEMP_CLAMP = (-50.0, 54.0)  # generated temperatures; +5 C silent shift stays inside -90..60
AR1_PHI = 0.7
AR1_SD = 2.5
STATION_JITTER_SD = 1.5


@dataclass(frozen=True)
class Climate:
    mean_max_c: float  # annual mean of daily max
    amplitude_c: float  # seasonal half-range of daily max
    diurnal_c: float  # mean max - min
    wet_prob: float  # P(precip > 0)
    wet_mean_mm: float  # mean precip on a wet day (exponential)
    southern: bool = False


CLIMATES: dict[str, Climate] = {
    "chennai": Climate(33.0, 3.0, 8.0, 0.25, 12.0),
    "mumbai": Climate(31.0, 2.0, 7.0, 0.35, 20.0),
    "reykjavik": Climate(7.0, 5.0, 5.0, 0.55, 3.0),
    "cairo": Climate(28.0, 7.0, 12.0, 0.03, 2.0),
    "singapore": Climate(31.0, 1.0, 7.0, 0.50, 9.0),
    "anchorage": Climate(6.0, 12.0, 7.0, 0.35, 3.0),
    "nairobi": Climate(24.0, 2.0, 11.0, 0.30, 6.0, southern=True),
    "london": Climate(15.0, 7.0, 7.0, 0.45, 3.0),
    "phoenix": Climate(30.0, 10.0, 14.0, 0.08, 4.0),
    "oslo": Climate(10.0, 11.0, 7.0, 0.40, 4.0),
}

# --- fault catalogue: 8 types x 3 severities x 5 seeds = 120 conditions ----------------

Severity = Literal["small", "medium", "large"]
SEVERITIES: tuple[Severity, ...] = ("small", "medium", "large")
FaultType = Literal[
    "schema_drift",
    "freshness_lapse",
    "volume",
    "null_spike",
    "duplicates",
    "out_of_range",
    "orphan_fk",
    "silent_shift",
]
FAULT_TYPES: tuple[FaultType, ...] = (
    "schema_drift",
    "freshness_lapse",
    "volume",
    "null_spike",
    "duplicates",
    "out_of_range",
    "orphan_fk",
    "silent_shift",
)

# What each (type, severity) does. Interpreted by faults.py; the labels are the contract.
FAULTS: dict[FaultType, dict[Severity, dict[str, object]]] = {
    "schema_drift": {
        "small": {"op": "retype", "column": "temp_max_c", "to": "float32"},
        "medium": {"op": "rename", "column": "temp_max_c", "to": "tmax_c"},
        "large": {"op": "drop", "column": "temp_max_c"},
    },
    # `missed` batches deliver nothing; the backlog arrives in the next batch.
    "freshness_lapse": {
        "small": {"missed": 1},
        "medium": {"missed": 3},
        "large": {"missed": 7},
    },
    # drop: a seeded fraction of stations is missing; spike: the previous batch's rows
    # are delivered again (an upstream retry), so the batch is ~2x and keys repeat.
    "volume": {
        "small": {"op": "drop", "keep": 0.8},
        "medium": {"op": "drop", "keep": 0.5},
        "large": {"op": "replay_previous", "factor": 2.0},
    },
    # An extra fraction of rows is nulled in one column, drawn per condition from `columns`.
    "null_spike": {
        "small": {"fraction": 0.05, "columns": ("temp_max_c", "precip_mm")},
        "medium": {"fraction": 0.20, "columns": ("temp_max_c", "precip_mm")},
        "large": {"fraction": 0.50, "columns": ("temp_max_c", "precip_mm")},
    },
    # Rows duplicated inside the batch: `rows` exact, or ceil(fraction * n).
    "duplicates": {
        "small": {"rows": 1},
        "medium": {"fraction": 0.01},
        "large": {"fraction": 0.10},
    },
    "out_of_range": {
        "small": {"op": "swap_min_max", "rows": 1},  # in range, but min > max
        "medium": {"op": "set_temp_max", "value": 200.0, "rows": 1},
        "large": {"op": "set_temp_max", "value": 200.0, "fraction": 0.10},
    },
    # city_id replaced by an id raw.cities does not have (a station sent before its row).
    "orphan_fk": {
        "small": {"rows": 1},
        "medium": {"fraction": 0.01},
        "large": {"fraction": 0.10},
    },
    # Every temperature shifted up; stays in range. The expected blind spot.
    "silent_shift": {
        "small": {"delta_c": 1.0},
        "medium": {"delta_c": 3.0},
        "large": {"delta_c": 5.0},
    },
}

FAULT_WINDOW = 7  # a fault is active for batches onset .. onset + 6
ONSET_RANGE = (EVAL_FIRST, N_BATCHES - FAULT_WINDOW + 1)  # inclusive: 15..54
SEEDS: tuple[int, ...] = (3601, 3602, 3603, 3604, 3605)
SMOKE_SEEDS: tuple[int, ...] = (3601,)

# --- threshold checks ------------------------------------------------------------------

K_DEFAULT = 4.0
K_GRID: tuple[float, ...] = (2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0, 8.0)
MAD_TO_SD = 1.4826
FA_TARGET_PER_100 = 1.0  # operating-point target on clean W1 streams
OPERATING_POINT_CHECKS: tuple[str, ...] = (
    "volume.rows",
    "null_rate.temp_max_c",
    "null_rate.precip_mm",
)
# H1 compares arms on these (fault, severity) cells, world W1.
H1_CELLS: tuple[tuple[FaultType, Severity], ...] = (("volume", "small"), ("null_spike", "small"))

# --- predicted detection (pre-registered; compared with what happens) -------------------
# Families expected to raise an attributable alarm, at k = 4, world W1.
# S1 is predicted identical to A. A family "detects" a cell when it does so in >= 3 of 5 seeds.

_SCHEMA_BREAK: frozenset[Family] = frozenset({"schema", "nulls", "range"})

EXPECTED: dict[Arm, dict[FaultType, dict[Severity, frozenset[Family]]]] = {
    "A": {
        "schema_drift": {
            "small": frozenset({"schema"}),
            "medium": _SCHEMA_BREAK,
            "large": _SCHEMA_BREAK,
        },
        "freshness_lapse": {s: frozenset({"freshness", "volume"}) for s in SEVERITIES},
        "volume": {
            "small": frozenset({"volume"}),
            "medium": frozenset({"volume"}),
            "large": frozenset({"volume", "unique"}),
        },
        "null_spike": {s: frozenset({"nulls"}) for s in SEVERITIES},
        "duplicates": {
            "small": frozenset({"unique"}),
            "medium": frozenset({"unique"}),
            "large": frozenset({"unique", "volume"}),
        },
        "out_of_range": {s: frozenset({"range"}) for s in SEVERITIES},
        "orphan_fk": {s: frozenset({"referential"}) for s in SEVERITIES},
        "silent_shift": {s: frozenset() for s in SEVERITIES},
    },
    "S0": {
        "schema_drift": {
            "small": frozenset(),
            "medium": frozenset({"schema", "range"}),
            "large": frozenset({"schema", "range"}),
        },
        "freshness_lapse": {
            "small": frozenset(),
            "medium": frozenset({"freshness"}),
            "large": frozenset({"freshness"}),
        },
        "volume": {
            "small": frozenset(),
            "medium": frozenset(),
            "large": frozenset({"unique"}),
        },
        "null_spike": {s: frozenset() for s in SEVERITIES},
        "duplicates": {s: frozenset({"unique"}) for s in SEVERITIES},
        "out_of_range": {s: frozenset({"range"}) for s in SEVERITIES},
        "orphan_fk": {s: frozenset({"referential"}) for s in SEVERITIES},
        "silent_shift": {s: frozenset() for s in SEVERITIES},
    },
}
EXPECTED["S1"] = EXPECTED["A"]
MAJORITY = 3  # of 5 seeds

# --- the #35 lake source ---------------------------------------------------------------

LAKE_TABLE = "lake.events"
# A 0-record snapshot followed by another snapshot within this many seconds is the first
# half of one overwrite commit (PyIceberg writes DELETE then APPEND). Measured 2026-10-23
# on #35 at 50bee72, 2,000 rows: 99 ms between the two.
PAIR_GAP_SECONDS = 60.0
LAKE_ROWS = 50_000
LAKE_K = 4.0
