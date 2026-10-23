import datetime as dt
import decimal

import numpy as np
import pandas as pd
import pytest

from dq.contract import (
    Contract,
    compare_columns,
    load_contract,
    logical_type,
    pandas_logical_type,
)

UTC = dt.UTC


def test_load_contract_reads_the_frozen_json():
    c = load_contract()
    assert isinstance(c, Contract)
    assert c.upstream["sha"].startswith("e20cecc")
    weather = c.tables["raw.weather_daily"]
    assert weather.rows == 7310
    assert weather.primary_key == ("city_id", "date")
    assert weather.foreign_keys == {"city_id": "raw.cities.city_id"}
    assert list(weather.columns) == [
        "city_id",
        "date",
        "temp_max_c",
        "temp_min_c",
        "precip_mm",
        "wind_max_kmh",
        "ingested_at",
    ]
    assert len(c.tables) == 8
    assert set(v for t in c.tables.values() for v in t.columns.values()) <= set(c.logical_types)


@pytest.mark.parametrize(
    ("engine_type", "expected"),
    [
        # DuckDB
        ("VARCHAR", "text"),
        ("DOUBLE", "double"),
        ("FLOAT", "real"),
        ("REAL", "real"),
        ("TINYINT", "integer"),
        ("SMALLINT", "integer"),
        ("INTEGER", "integer"),
        ("BIGINT", "integer"),
        ("HUGEINT", "integer"),
        ("UTINYINT", "integer"),
        ("UBIGINT", "integer"),
        ("DECIMAL(18,3)", "numeric"),
        ("DECIMAL(18,1)", "numeric"),
        ("DATE", "date"),
        ("TIMESTAMP WITH TIME ZONE", "timestamptz"),
        ("BOOLEAN", "boolean"),
        # Postgres information_schema.columns.data_type
        ("text", "text"),
        ("character varying", "text"),
        ("double precision", "double"),
        ("real", "real"),
        ("smallint", "integer"),
        ("integer", "integer"),
        ("bigint", "integer"),
        ("numeric", "numeric"),
        ("date", "date"),
        ("timestamp with time zone", "timestamptz"),
        ("boolean", "boolean"),
    ],
)
def test_logical_type(engine_type, expected):
    assert logical_type(engine_type) == expected


@pytest.mark.parametrize("name", ["GEOMETRY", "timestamp without time zone", "uuid", ""])
def test_logical_type_rejects_unknown_names(name):
    with pytest.raises(ValueError):
        logical_type(name)


def _utc_series(*values):
    return pd.Series(list(values)).astype("datetime64[us, UTC]")


@pytest.mark.parametrize(
    ("series", "expected"),
    [
        (pd.Series([1.5, np.nan], dtype="float64"), "double"),
        (pd.Series([1.5], dtype="float32"), "real"),
        (pd.Series([1, 2], dtype="int64"), "integer"),
        (pd.Series([1, 2], dtype="int32"), "integer"),
        (pd.Series([1, None], dtype="Int64"), "integer"),
        (pd.Series([True, False]), "boolean"),
        (_utc_series(dt.datetime(2024, 1, 1, tzinfo=UTC)), "timestamptz"),
        (pd.Series(["a", "b"], dtype="str"), "text"),
        (pd.Series(["a", None], dtype="string"), "text"),
        (pd.Series(["a", "b"], dtype=object), "text"),
        (pd.Series([dt.date(2024, 1, 1)], dtype=object), "date"),
        (pd.Series([None, dt.date(2024, 1, 1)], dtype=object), "date"),
        (pd.Series([dt.datetime(2024, 1, 1, tzinfo=UTC)], dtype=object), "timestamptz"),
        (pd.Series([decimal.Decimal("1.5")], dtype=object), "numeric"),
        (pd.Series([], dtype=object), "unknown"),
        (pd.Series([None, None], dtype=object), "unknown"),
    ],
)
def test_pandas_logical_type(series, expected):
    assert pandas_logical_type(series) == expected


def test_a_datetime_is_not_a_date():
    # datetime subclasses date; an object column of datetimes must not read as "date"
    s = pd.Series([dt.datetime(2024, 1, 1, tzinfo=UTC)], dtype=object)
    assert pandas_logical_type(s) != "date"


def test_naive_datetime_is_a_difference_not_an_unknown():
    s = pd.Series(pd.to_datetime(["2024-01-01"]))
    assert pandas_logical_type(s) == "timestamp"


def test_compare_columns_equal():
    cols = {"a": "text", "b": "double"}
    assert compare_columns(cols, dict(cols)) == []


def test_compare_columns_reports_every_difference_kind():
    diffs = compare_columns(
        {"a": "text", "b": "double", "c": "date"},
        {"a": "text", "b": "real", "d": "integer"},
    )
    assert sorted(diffs) == [
        "column b: expected double, got real",
        "missing column c",
        "unexpected column d",
    ]


def test_unknown_matches_anything_but_a_missing_column_still_fails():
    assert compare_columns({"a": "date"}, {"a": "unknown"}) == []
    assert compare_columns({"a": "date"}, {}) == ["missing column a"]
