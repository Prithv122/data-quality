import datetime as dt
import shutil
import subprocess
from pathlib import Path

import pandas as pd
import pytest

from dq import fixture, spec, sql22
from dq.contract import load_contract
from dq.warehouse import DuckDBWarehouse

UPSTREAM = Path(__file__).resolve().parents[2] / "22-elt-dbt-dagster"
HEADER_PREFIX = "-- Vendored from 22-elt-dbt-dagster"


@pytest.fixture(scope="module")
def warehouse(tmp_path_factory):
    path = fixture.build_fixture(tmp_path_factory.mktemp("fx") / "w.duckdb")
    with DuckDBWarehouse(str(path)) as wh:
        yield wh


# --- G1: the fixture equals the contract -----------------------------------------------


def test_every_table_matches_the_contract_exactly(warehouse):
    contract = load_contract()
    for name, table in contract.tables.items():
        assert warehouse.columns(name) == table.columns, name
        assert list(warehouse.columns(name)) == list(table.columns), f"{name}: column order"


def test_row_counts(warehouse):
    assert warehouse.fetch_df("SELECT count(*) AS n FROM raw.cities")["n"][0] == 10
    assert warehouse.fetch_df("SELECT count(*) AS n FROM raw.weather_daily")["n"][0] == 7310
    days = (spec.FIXTURE_END - spec.FIXTURE_START).days + 1
    assert days == 731


def test_primary_and_foreign_keys_hold(warehouse):
    dup = warehouse.fetch_df(
        "SELECT count(*) AS n FROM (SELECT city_id, date FROM raw.weather_daily "
        "GROUP BY 1, 2 HAVING count(*) > 1)"
    )
    orphans = warehouse.fetch_df(
        "SELECT count(*) AS n FROM raw.weather_daily w "
        "LEFT JOIN raw.cities c USING (city_id) WHERE c.city_id IS NULL"
    )
    bad_pair = warehouse.fetch_df(
        "SELECT count(*) AS n FROM raw.weather_daily WHERE temp_min_c > temp_max_c"
    )
    nulls = warehouse.fetch_df(
        "SELECT count(*) AS n FROM raw.weather_daily WHERE temp_max_c IS NULL "
        "OR temp_min_c IS NULL OR precip_mm IS NULL OR wind_max_kmh IS NULL"
    )
    assert (dup["n"][0], orphans["n"][0], bad_pair["n"][0], nulls["n"][0]) == (0, 0, 0, 0)


def test_the_database_enforces_the_keys(tmp_path):
    import duckdb

    path = fixture.build_fixture(tmp_path / "k.duckdb")
    con = duckdb.connect(str(path))
    try:
        with pytest.raises(duckdb.Error):
            con.execute(
                "INSERT INTO raw.weather_daily SELECT * FROM raw.weather_daily LIMIT 1"
            )  # duplicate primary key
        with pytest.raises(duckdb.Error):
            con.execute(
                "INSERT INTO raw.weather_daily VALUES "
                "('nowhere', DATE '2030-01-01', 1, 0, 0, 0, now())"
            )  # unknown city
        with pytest.raises(duckdb.Error):
            con.execute(
                "INSERT INTO raw.weather_daily VALUES ('oslo', DATE '2030-01-01', 1, 0, 0, 0, NULL)"
            )  # ingested_at is NOT NULL
    finally:
        con.close()


def test_all_six_models_build_and_are_not_empty(warehouse):
    assert len(sql22.MODELS) == 6
    for schema, model in sql22.MODELS:
        n = warehouse.fetch_df(f"SELECT count(*) AS n FROM {schema}.{model}")["n"][0]
        assert n > 0, model
    assert (
        warehouse.fetch_df("SELECT count(*) AS n FROM public_staging.stg_weather_daily")["n"][0]
        == 7310
    )
    assert (
        warehouse.fetch_df("SELECT count(*) AS n FROM public_marts.marts_city_monthly_climate")[
            "n"
        ][0]
        == 240
    )  # 10 cities x 24 months
    assert (
        warehouse.fetch_df("SELECT count(*) AS n FROM public_marts.marts_global_daily_extremes")[
            "n"
        ][0]
        == 731
    )


def test_build_is_deterministic_and_overwrites(tmp_path):
    a = fixture.build_fixture(tmp_path / "a.duckdb")
    fixture.build_fixture(a)  # second build onto the same file must not fail or double the rows
    with DuckDBWarehouse(str(a)) as wh:
        assert wh.fetch_df("SELECT count(*) AS n FROM raw.weather_daily")["n"][0] == 7310
        first = wh.fetch_df("SELECT * FROM raw.weather_daily ORDER BY city_id, date")
    b = fixture.build_fixture(tmp_path / "b.duckdb")
    with DuckDBWarehouse(str(b)) as wh:
        second = wh.fetch_df("SELECT * FROM raw.weather_daily ORDER BY city_id, date")
    pd.testing.assert_frame_equal(first, second)


# --- generators ------------------------------------------------------------------------


def test_seed_cities():
    c = fixture.seed_cities()
    assert len(c) == 10
    assert list(c["city_id"]) == list(spec.CLIMATES)
    assert (c["loaded_at"] == pd.Timestamp("2025-01-01", tz="UTC")).all()


def test_generate_weather_shape_and_dtypes():
    cities = fixture.seed_cities()
    w = fixture.generate_weather(cities, dt.date(2024, 2, 27), dt.date(2024, 3, 2), seed=1)
    assert len(w) == 10 * 5  # leap year: 27, 28, 29, 1, 2
    assert list(w.columns) == [
        "city_id",
        "date",
        "temp_max_c",
        "temp_min_c",
        "precip_mm",
        "wind_max_kmh",
        "ingested_at",
    ]
    assert w.isna().sum().sum() == 0
    assert all(type(d) is dt.date for d in w["date"])
    assert str(w["ingested_at"].dtype) == "datetime64[us, UTC]"
    assert w.loc[0, "ingested_at"] == pd.Timestamp("2024-02-28 02:00", tz="UTC")
    assert not w.duplicated(["city_id", "date"]).any()
    assert (w["temp_min_c"] <= w["temp_max_c"]).all()


def test_generate_weather_is_seeded_and_cities_are_independent():
    cities = fixture.seed_cities()
    d = dt.date(2024, 1, 1)
    a = fixture.generate_weather(cities, d, d + dt.timedelta(days=9), seed=7)
    b = fixture.generate_weather(cities, d, d + dt.timedelta(days=9), seed=7)
    c = fixture.generate_weather(cities, d, d + dt.timedelta(days=9), seed=8)
    pd.testing.assert_frame_equal(a, b)
    assert not a["temp_max_c"].equals(c["temp_max_c"])


# --- vendored SQL ----------------------------------------------------------------------


@pytest.mark.parametrize(("schema", "model"), sql22.MODELS)
def test_vendored_sql_starts_with_the_attribution_header(schema, model):
    text = sql22.source_text(model)
    header, _, _ = text.partition("\n")
    assert header.startswith(HEADER_PREFIX)
    assert "commit e20cecc" in header
    assert "MIT" in header


def test_render_resolves_source_and_ref():
    stg = sql22.render("stg_weather_daily")
    assert "raw.weather_daily" in stg
    assert "{{" not in stg
    enriched = sql22.render("int_weather_enriched")
    assert "public_staging.stg_weather_daily" in enriched
    assert "public_staging.stg_cities" in enriched
    assert "public_intermediate.int_weather_enriched" in sql22.render("marts_city_monthly_climate")
    assert "raw.cities" in sql22.render("stg_cities")
    for _, model in sql22.MODELS:
        assert "{{" not in sql22.render(model)


def test_render_rejects_an_unknown_model():
    with pytest.raises(KeyError):
        sql22.render("nope")


def test_models_are_listed_in_build_order():
    seen = set()
    for _, model in sql22.MODELS:
        for _, dep in sql22.MODELS:
            if f"{{{{ ref('{dep}') }}}}" in sql22.source_text(model):
                assert dep in seen, f"{model} built before {dep}"
        seen.add(model)


@pytest.mark.skipif(
    not (UPSTREAM / ".git").exists() or shutil.which("git") is None,
    reason="the sibling repo 22-elt-dbt-dagster is not checked out here",
)
def test_vendored_files_equal_upstream_at_the_pinned_commit():
    def show(path: str) -> str:
        out = subprocess.run(
            ["git", "-C", str(UPSTREAM), "show", f"e20cecc:{path}"],
            capture_output=True,
            check=True,
        )
        return out.stdout.decode("utf-8")

    for schema, model in sql22.MODELS:
        folder = schema.removeprefix("public_")
        upstream = show(f"dbt_project/models/{folder}/{model}.sql")
        _, _, body = sql22.source_text(model).partition("\n")
        assert body == upstream, model
    assert sql22.CITIES_CSV.read_bytes().decode("utf-8") == show("data/seed/cities.csv")
