"""Column layout of every summary table: written by evaluate/lake, stored, read by the dashboard."""

from __future__ import annotations

import pandas as pd

SUMMARY_COLUMNS: dict[str, tuple[str, ...]] = {
    # one row per (world, arm, k, fault, severity); k is NaN for arm S0
    "recall": (
        "world", "arm", "k", "fault", "severity", "detected", "n", "recall", "ci_low", "ci_high",
    ),
    # which families produced an attributable alarm, per cell
    "recall_by_family": ("world", "arm", "k", "fault", "severity", "family", "detected", "n"),
    # clean streams, scored batches only
    "false_alarms": (
        "world", "arm", "k", "check", "family", "alarms", "batch_checks", "per_100",
    ),
    "ttd": ("world", "arm", "k", "fault", "severity", "n_detected", "ttd_median", "ttd_max"),
    "operating_points": ("world", "arm", "k", "fa_per_100", "reached_target"),
    "verdicts": ("hypothesis", "world", "verdict", "detail"),
    # predicted vs observed families, k = 4, W1; only cells that differ
    "mismatches": ("arm", "fault", "severity", "family", "predicted", "seeds_detected", "n"),
    # alarms after a fault window closes that the clean twin does not have
    "post_fault_alarms": ("world", "arm", "k", "fault", "severity", "alarms"),
    # lake source: one row per (walk, logical version, check)
    "lake": (
        "walk", "version", "snapshot_id", "kind", "check", "family", "status", "observed",
        "expected", "detail",
    ),
}  # fmt: skip


def empty(name: str) -> pd.DataFrame:
    return pd.DataFrame({c: [] for c in SUMMARY_COLUMNS[name]})


def validate(name: str, df: pd.DataFrame) -> pd.DataFrame:
    """Return `df` with exactly the declared columns, in order; raise if any is missing."""
    cols = SUMMARY_COLUMNS[name]
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"summary {name!r} is missing columns {missing}")
    return df.loc[:, list(cols)]
