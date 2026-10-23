"""The #22 fixture warehouse: `raw` from a seeded generator, models built by #22's own SQL."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from dq import spec, sql22
from dq.weathergen import station_series

SEED_LOADED_AT = dt.datetime(2025, 1, 1, tzinfo=dt.UTC)

# #22's DDL (src/elt_dbt_dagster/load.py @ e20cecc) with DuckDB type names (DOUBLE PRECISION
# becomes DOUBLE, TEXT becomes VARCHAR). DEFAULT now() is dropped: the fixture always supplies
# loaded_at and ingested_at.
_DDL = """
CREATE SCHEMA raw;
CREATE SCHEMA public_staging;
CREATE SCHEMA public_intermediate;
CREATE SCHEMA public_marts;

CREATE TABLE raw.cities (
    city_id       VARCHAR PRIMARY KEY,
    city_name     VARCHAR NOT NULL,
    country       VARCHAR NOT NULL,
    latitude      DOUBLE NOT NULL,
    longitude     DOUBLE NOT NULL,
    population    BIGINT NOT NULL,
    climate_zone  VARCHAR NOT NULL,
    loaded_at     TIMESTAMPTZ NOT NULL
);

CREATE TABLE raw.weather_daily (
    city_id       VARCHAR NOT NULL REFERENCES raw.cities (city_id),
    date          DATE NOT NULL,
    temp_max_c    DOUBLE,
    temp_min_c    DOUBLE,
    precip_mm     DOUBLE,
    wind_max_kmh  DOUBLE,
    ingested_at   TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (city_id, date)
);
"""


def seed_cities() -> pd.DataFrame:
    """#22's ten seed cities, `loaded_at` fixed so the fixture is reproducible."""
    df = pd.read_csv(sql22.CITIES_CSV, dtype={"city_id": "object"})
    for col in ("city_id", "city_name", "country", "climate_zone"):
        df[col] = df[col].astype(object)
    df["loaded_at"] = pd.Series([SEED_LOADED_AT] * len(df)).astype("datetime64[us, UTC]")
    return df


def generate_weather(cities: pd.DataFrame, start: dt.date, end: dt.date, seed: int) -> pd.DataFrame:
    """One row per (city, day) from `start` to `end` inclusive, no nulls.

    Each city gets its own generator derived from (seed, position), so adding a city never
    changes another city's series.
    """
    n_days = (end - start).days + 1
    dates = [start + dt.timedelta(days=i) for i in range(n_days)]
    ingested = pd.Series(
        [dt.datetime.combine(d, dt.time(), tzinfo=dt.UTC) + spec.INGEST_OFFSET for d in dates]
    ).astype("datetime64[us, UTC]")

    frames = []
    for index, city_id in enumerate(cities["city_id"]):
        rng = np.random.default_rng([seed, index])
        series = station_series(rng, spec.CLIMATES[city_id], dates)
        frame = pd.DataFrame({"city_id": pd.Series([city_id] * n_days, dtype=object)})
        frame["date"] = pd.Series(dates, dtype=object)
        for col, values in series.items():
            frame[col] = values.astype("float64")
        frame["ingested_at"] = ingested
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)


def build_fixture(path: str | Path, seed: int = spec.FIXTURE_SEED) -> Path:
    """Create (or overwrite) the DuckDB fixture at `path` and build every #22 model in it."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    path.with_name(path.name + ".wal").unlink(missing_ok=True)

    cities = seed_cities()
    weather = generate_weather(cities, spec.FIXTURE_START, spec.FIXTURE_END, seed)

    con = duckdb.connect(str(path))
    try:
        con.execute(_DDL)
        con.register("cities_df", cities)
        con.execute("INSERT INTO raw.cities SELECT * FROM cities_df")
        con.register("weather_df", weather)
        con.execute(
            "INSERT INTO raw.weather_daily "
            "SELECT city_id, CAST(date AS DATE), temp_max_c, temp_min_c, precip_mm, "
            "wind_max_kmh, ingested_at FROM weather_df"
        )
        for schema, model in sql22.MODELS:
            con.execute(f"CREATE TABLE {schema}.{model} AS\n{sql22.render(model)}")
    finally:
        con.close()
    return path
