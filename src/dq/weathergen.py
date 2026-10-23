"""Daily weather for one station: the generator both the #22 fixture and the stream use."""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Sequence

import numpy as np

from dq.spec import AR1_PHI, AR1_SD, TEMP_CLAMP, Climate


def station_series(
    rng: np.random.Generator,
    climate: Climate,
    dates: Sequence[dt.date],
    mean_shift: float = 0.0,
) -> dict[str, np.ndarray]:
    """temp_max_c, temp_min_c, precip_mm, wind_max_kmh for consecutive `dates`, no nulls.

    Guarantees temp_min_c <= temp_max_c and both inside TEMP_CLAMP, so clean rows pass every
    #22 test. Values are rounded to 0.1 like the Open-Meteo archive.
    """
    n = len(dates)
    doy = np.array([d.timetuple().tm_yday for d in dates], dtype=float)
    phase = 2 * math.pi * (doy - 200.0) / 365.25  # northern peak in mid-July
    if climate.southern:
        phase = phase + math.pi
    seasonal = climate.mean_max_c + mean_shift + climate.amplitude_c * np.cos(phase)

    noise = np.empty(n)
    prev = rng.normal(0.0, AR1_SD / math.sqrt(1 - AR1_PHI**2))
    for i in range(n):
        prev = AR1_PHI * prev + rng.normal(0.0, AR1_SD)
        noise[i] = prev

    lo, hi = TEMP_CLAMP
    t_max = np.round(np.clip(seasonal + noise, lo, hi), 1)
    diurnal = np.clip(rng.normal(climate.diurnal_c, 1.5, n), 0.5, None)
    t_min = np.round(np.maximum(t_max - diurnal, lo), 1)
    t_min = np.minimum(t_min, t_max)  # rounding can never push min above max

    wet = rng.random(n) < climate.wet_prob
    precip = np.round(np.where(wet, rng.exponential(climate.wet_mean_mm, n), 0.0), 1)
    wind = np.round(rng.gamma(4.0, 4.0, n), 1)
    return {"temp_max_c": t_max, "temp_min_c": t_min, "precip_mm": precip, "wind_max_kmh": wind}
