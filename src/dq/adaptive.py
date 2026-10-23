"""Threshold checks: a scalar metric judged against a baseline of past batches.

Both arms use the same rule, `median +/- k * max(1.4826 * MAD, floor)`. They differ only in
which batches form the baseline:

- frozen  (arm S1): the warm-up batches, fixed for the whole stream;
- rolling (arm A):  the last `window` accepted batches before this one, where accepted means
  the metric was computed and the batch did not alarm (so a fault does not teach the
  baseline that it is normal).
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from dq.spec import BASELINE_WINDOW, MAD_TO_SD, MIN_BASELINE, WARMUP_BATCHES
from dq.types import MetricValue, Status

Mode = Literal["frozen", "rolling"]
Side = Literal["upper", "two_sided"]


@dataclass(frozen=True)
class ThresholdRule:
    k: float
    side: Side
    mode: Mode
    floor: float = 0.0  # absolute floor on the scale
    floor_relative: float = 0.0  # floor as a fraction of |median|
    window: int = BASELINE_WINDOW
    warmup: int = WARMUP_BATCHES
    min_baseline: int = MIN_BASELINE


@dataclass(frozen=True)
class Decision:
    status: Status
    observed: float | None
    median: float | None = None
    scale: float | None = None
    expected: str = ""
    detail: str = ""


def baseline_stats(values: Sequence[float], rule: ThresholdRule) -> tuple[float, float]:
    """(median, scale) of a baseline, scale = max(1.4826 * MAD, floors)."""
    med = statistics.median(values)
    mad = statistics.median(abs(v - med) for v in values)
    scale = max(MAD_TO_SD * mad, rule.floor, rule.floor_relative * abs(med))
    return med, scale


def judge(x: float, med: float, scale: float, rule: ThresholdRule) -> tuple[bool, str]:
    """(alarm, rule text) for one observation."""
    hi = med + rule.k * scale
    if rule.side == "upper":
        return x > hi, f"<= {hi:.6g} (median {med:.6g} + {rule.k:g} x {scale:.6g})"
    lo = med - rule.k * scale
    text = f"in [{lo:.6g}, {hi:.6g}] (median {med:.6g} +/- {rule.k:g} x {scale:.6g})"
    return (x > hi or x < lo), text


def evaluate_series(metrics: Sequence[MetricValue], rule: ThresholdRule) -> list[Decision]:
    """One Decision per batch, in order. Index 0 is batch 1.

    Warm-up batches are never judged (status "skipped"). A metric that could not be computed
    is an "error" (an alarm) or "skipped", as reported, and never enters the baseline.
    """
    out: list[Decision] = []
    accepted: list[float] = []  # metric values eligible for the baseline, oldest first
    frozen: tuple[float, float] | None = None

    for i, m in enumerate(metrics):
        batch = i + 1
        if batch <= rule.warmup:
            if m.status == "ok" and m.value is not None:
                accepted.append(m.value)
            out.append(Decision("skipped", m.value, detail="warm-up"))
            continue

        if rule.mode == "frozen" and frozen is None:
            if len(accepted) < rule.min_baseline:
                frozen = (float("nan"), float("nan"))
            else:
                frozen = baseline_stats(accepted, rule)

        if m.status != "ok" or m.value is None:
            out.append(Decision(m.status, None, detail=m.detail))
            continue

        if rule.mode == "frozen":
            assert frozen is not None
            med, scale = frozen
            if med != med:  # nan: warm-up had too few usable batches
                out.append(Decision("skipped", m.value, detail="insufficient baseline"))
                continue
        else:
            base = accepted[-rule.window :]
            if len(base) < rule.min_baseline:
                out.append(Decision("skipped", m.value, detail="insufficient baseline"))
                continue
            med, scale = baseline_stats(base, rule)

        alarm, text = judge(m.value, med, scale, rule)
        out.append(Decision("fail" if alarm else "pass", m.value, med, scale, text))
        if not alarm:
            accepted.append(m.value)

    return out
