"""The simulated batch stream: a station network, outages, late rows, base nulls, W1 growth.

Every station draws from its own random stream, keyed by (seed, city, station number), so
onboarding extra stations in W1 never changes what the other stations produce: W0 and W1 of
one seed differ only by the onboarded stations' rows.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from dq import spec
from dq.weathergen import station_series

# #22's ten seed cities (raw.cities at e20cecc). city_id doubles as the key into spec.CLIMATES.
SEED_CITIES: tuple[tuple[str, str, str, float, float, int, str], ...] = (
    ("chennai", "Chennai", "India", 13.0827, 80.2707, 7100000, "tropical_savanna"),
    ("mumbai", "Mumbai", "India", 19.0760, 72.8777, 12400000, "tropical_monsoon"),
    ("reykjavik", "Reykjavik", "Iceland", 64.1466, -21.9426, 140000, "subarctic"),
    ("cairo", "Cairo", "Egypt", 30.0444, 31.2357, 10100000, "hot_desert"),
    ("singapore", "Singapore", "Singapore", 1.3521, 103.8198, 5900000, "tropical_rainforest"),
    ("anchorage", "Anchorage", "United States", 61.2181, -149.9003, 290000, "subarctic"),
    ("nairobi", "Nairobi", "Kenya", -1.2921, 36.8219, 4400000, "subtropical_highland"),
    ("london", "London", "United Kingdom", 51.5072, -0.1276, 8900000, "temperate_oceanic"),
    ("phoenix", "Phoenix", "United States", 33.4484, -112.0740, 1680000, "hot_desert"),
    ("oslo", "Oslo", "Norway", 59.9139, 10.7522, 700000, "humid_continental"),
)

WEATHER_COLUMNS = (
    "city_id",
    "date",
    "temp_max_c",
    "temp_min_c",
    "precip_mm",
    "wind_max_kmh",
    "ingested_at",
)
VALUE_COLUMNS = ("temp_max_c", "temp_min_c", "precip_mm", "wind_max_kmh")
CITY_COLUMNS = (
    "city_id",
    "city_name",
    "country",
    "latitude",
    "longitude",
    "population",
    "climate_zone",
    "loaded_at",
    "onboard_batch",
)


@dataclass
class Stream:
    seed: int
    world: str
    batches: list[pd.DataFrame]  # index 0 = batch 1: exactly the rows landing in that batch
    cities: pd.DataFrame  # one row per station, with the batch it was onboarded in
    ingest_times: list[dt.datetime]
    check_times: list[dt.datetime]
    _parents: dict[int, frozenset[str]] = field(default_factory=dict, repr=False, compare=False)

    def parent_keys(self, batch: int) -> frozenset[str]:
        """Stations registered in raw.cities when `batch` (1-based) lands."""
        if batch not in self._parents:
            mask = self.cities["onboard_batch"] <= batch
            self._parents[batch] = frozenset(self.cities.loc[mask, "city_id"])
        return self._parents[batch]

    @property
    def n_batches(self) -> int:
        return len(self.batches)


def data_date(batch: int) -> dt.date:
    return spec.STREAM_START + dt.timedelta(days=batch - 1)


def _at_midnight_utc(d: dt.date) -> dt.datetime:
    return dt.datetime.combine(d, dt.time(0), tzinfo=dt.UTC)


def ingest_time(batch: int) -> dt.datetime:
    return _at_midnight_utc(data_date(batch)) + spec.INGEST_OFFSET


def check_time(batch: int) -> dt.datetime:
    return _at_midnight_utc(data_date(batch)) + spec.CHECK_OFFSET


def timestamps(ts: dt.datetime, n: int) -> pd.DatetimeIndex:
    """`n` copies of `ts` as datetime64[us, UTC]."""
    return pd.DatetimeIndex([ts] * n, tz=dt.UTC).as_unit("us")


def _stations(world: str) -> list[tuple[str, int, int, int]]:
    """(station id, city index, station number, onboard batch), initial stations first."""
    out = []
    for ci, row in enumerate(SEED_CITIES):
        for num in range(1, spec.STATIONS_PER_CITY + 1):
            out.append((f"{row[0]}-{num:02d}", ci, num, 1))
    if world == "W1":
        n_cities = len(SEED_CITIES)
        for i, b in enumerate(range(spec.ONBOARD_EVERY, spec.N_BATCHES + 1, spec.ONBOARD_EVERY)):
            ci = i % n_cities
            num = spec.STATIONS_PER_CITY + i // n_cities + 1
            out.append((f"{SEED_CITIES[ci][0]}-{num:02d}", ci, num, b))
    return out


def _cities_frame(stations: list[tuple[str, int, int, int]]) -> pd.DataFrame:
    rows = []
    for sid, ci, _num, onboard in stations:
        c = SEED_CITIES[ci]
        rows.append(
            {
                "city_id": sid,
                "city_name": c[1],
                "country": c[2],
                "latitude": c[3],
                "longitude": c[4],
                "population": c[5],
                "climate_zone": c[6],
                "loaded_at": ingest_time(onboard),
                "onboard_batch": onboard,
            }
        )
    df = pd.DataFrame(rows, columns=list(CITY_COLUMNS))
    return df.astype(
        {
            "city_id": "str",
            "city_name": "str",
            "country": "str",
            "latitude": "float64",
            "longitude": "float64",
            "population": "int64",
            "climate_zone": "str",
            "loaded_at": "datetime64[us, UTC]",
            "onboard_batch": "int64",
        }
    )


def _station_rows(seed: int, station: tuple[str, int, int, int]) -> pd.DataFrame:
    """All of one station's delivered rows, with the batch each lands in (`arrival`)."""
    sid, ci, num, onboard = station
    n = spec.N_BATCHES
    rng = np.random.default_rng([seed, ci, num])
    climate = spec.CLIMATES[SEED_CITIES[ci][0]]
    dates = [data_date(b) for b in range(1, n + 1)]
    jitter = float(rng.normal(0.0, spec.STATION_JITTER_SD))
    series = station_series(rng, climate, dates, mean_shift=jitter)

    # The same number of draws whatever the onboard batch, so a station is one fixed history.
    lost = rng.random(n) < spec.P_OUTAGE
    late = (rng.random(n) < spec.P_LATE) & ~lost
    null_draw = {col: rng.random(n) < spec.BASE_NULL_RATE[col] for col in VALUE_COLUMNS}

    day = np.arange(1, n + 1)
    arrival = day + late.astype(int)
    keep = (day >= onboard) & ~lost & (arrival <= n)

    data: dict[str, object] = {"city_id": [sid] * int(keep.sum()), "date": np.array(dates)[keep]}
    for col in VALUE_COLUMNS:
        values = series[col].astype("float64").copy()
        values[null_draw[col]] = np.nan
        data[col] = values[keep]
    data["arrival"] = arrival[keep]
    return pd.DataFrame(data)


def generate_stream(seed: int, world: str) -> Stream:
    if world not in spec.WORLDS:
        raise ValueError(f"unknown world {world!r}")
    stations = _stations(world)
    cities = _cities_frame(stations)
    ingest = [ingest_time(b) for b in range(1, spec.N_BATCHES + 1)]
    checks = [check_time(b) for b in range(1, spec.N_BATCHES + 1)]

    frames = [_station_rows(seed, s) for s in stations]
    allrows = pd.concat(frames, ignore_index=True)

    by_batch = {int(b): g for b, g in allrows.groupby("arrival", sort=True)}
    empty = allrows.iloc[0:0]
    batches: list[pd.DataFrame] = []
    for b in range(1, spec.N_BATCHES + 1):
        part = by_batch.get(b, empty).loc[:, list(WEATHER_COLUMNS[:-1])]
        part = part.reset_index(drop=True)
        part["city_id"] = part["city_id"].astype("str")
        part["date"] = part["date"].astype(object)
        part["ingested_at"] = timestamps(ingest[b - 1], len(part))
        batches.append(part.loc[:, list(WEATHER_COLUMNS)])
    return Stream(
        seed=seed,
        world=world,
        batches=batches,
        cities=cities,
        ingest_times=ingest,
        check_times=checks,
    )
