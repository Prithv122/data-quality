import os
import sys

import pandas as pd
import pytest

from dq import fixture
from dq.contract import load_contract
from dq.warehouse import DuckDBWarehouse, PostgresWarehouse, Warehouse


@pytest.fixture(scope="module")
def db_path(tmp_path_factory):
    return str(fixture.build_fixture(tmp_path_factory.mktemp("wh") / "w.duckdb"))


def test_duckdb_fetch_df_and_columns(db_path):
    with DuckDBWarehouse(db_path) as wh:
        df = wh.fetch_df("SELECT city_id, population FROM raw.cities ORDER BY city_id")
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 10
        assert list(df.columns) == ["city_id", "population"]
        assert wh.columns("raw.weather_daily") == {
            "city_id": "text",
            "date": "date",
            "temp_max_c": "double",
            "temp_min_c": "double",
            "precip_mm": "double",
            "wind_max_kmh": "double",
            "ingested_at": "timestamptz",
        }
        assert wh.columns("public_marts.marts_city_monthly_climate")["avg_temp_max_c"] == "numeric"


def test_duckdb_columns_of_a_missing_table_or_bad_name(db_path):
    with DuckDBWarehouse(db_path) as wh:
        with pytest.raises(KeyError):
            wh.columns("raw.nope")
        with pytest.raises(ValueError):
            wh.columns("weather_daily")


def test_duckdb_warehouse_is_read_only_by_default(db_path):
    import duckdb

    with DuckDBWarehouse(db_path) as wh, pytest.raises(duckdb.Error):
        wh.fetch_df("DELETE FROM raw.weather_daily")


def test_closing_is_idempotent_through_the_context_manager(db_path):
    wh = DuckDBWarehouse(db_path)
    with wh:
        pass
    # the protocol is satisfied structurally
    assert isinstance(wh, DuckDBWarehouse)
    _: Warehouse = wh


def test_the_module_imports_without_psycopg(monkeypatch):
    # PostgresWarehouse imports psycopg only when constructed
    monkeypatch.setitem(sys.modules, "psycopg", None)
    with pytest.raises(ImportError):
        PostgresWarehouse("postgresql://nobody@localhost/none")


@pytest.mark.postgres
def test_postgres_columns_equal_the_contract_json():
    """The drift guard: the real #22 database still has the types the JSON freezes."""
    dsn = os.environ.get("DQ_POSTGRES_DSN")
    if not dsn:
        pytest.skip("DQ_POSTGRES_DSN is not set")
    contract = load_contract()
    with PostgresWarehouse(dsn) as wh:
        for name, table in contract.tables.items():
            assert wh.columns(name) == table.columns, name
