"""The upstream (#22) contract and the one vocabulary every engine's types are mapped to.

DuckDB, Postgres and pandas name the same column type differently (`VARCHAR`, `character
varying`, `str`). Comparing them needs a shared vocabulary; these are the logical types the
contract JSON uses.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd

DEFAULT_CONTRACT = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "contract_22.json"


@dataclass(frozen=True)
class TableContract:
    name: str  # "raw.weather_daily"
    columns: dict[str, str]  # column -> logical type, in table order
    rows: int | None = None  # row count of the real fixture, where the contract states one
    primary_key: tuple[str, ...] = ()
    foreign_keys: dict[str, str] = field(default_factory=dict)  # column -> "schema.table.column"


@dataclass(frozen=True)
class Contract:
    upstream: dict[str, str]
    logical_types: tuple[str, ...]
    freshness: dict[str, Any]
    tables: dict[str, TableContract]


def load_contract(path: str | Path | None = None) -> Contract:
    raw = json.loads(Path(path or DEFAULT_CONTRACT).read_text(encoding="utf-8"))
    tables = {
        name: TableContract(
            name=name,
            columns=dict(t["columns"]),
            rows=t.get("rows"),
            primary_key=tuple(t.get("primary_key", ())),
            foreign_keys=dict(t.get("foreign_keys", {})),
        )
        for name, t in raw["tables"].items()
    }
    return Contract(
        upstream=raw["upstream"],
        logical_types=tuple(raw["logical_types"]),
        freshness=raw["freshness"],
        tables=tables,
    )


_INTEGERS = {
    "tinyint", "smallint", "integer", "int", "bigint", "hugeint", "int1", "int2", "int4",
    "int8", "utinyint", "usmallint", "uinteger", "ubigint", "uhugeint",
}  # fmt: skip
_TYPES = {
    "varchar": "text",
    "text": "text",
    "string": "text",
    "character varying": "text",
    "character": "text",
    "char": "text",
    "double": "double",
    "double precision": "double",
    "float8": "double",
    "float": "real",
    "real": "real",
    "float4": "real",
    "numeric": "numeric",
    "decimal": "numeric",
    "date": "date",
    "timestamp with time zone": "timestamptz",
    "timestamptz": "timestamptz",
    "boolean": "boolean",
    "bool": "boolean",
}
_PARAMS = re.compile(r"\s*\(.*\)\s*$")


def logical_type(engine_type: str) -> str:
    """Map a DuckDB type name or a Postgres `information_schema` data_type to a logical type."""
    key = _PARAMS.sub("", engine_type.strip().lower())  # DECIMAL(18,3), character varying(20)
    if key in _INTEGERS:
        return "integer"
    try:
        return _TYPES[key]
    except KeyError:
        raise ValueError(f"unknown engine type {engine_type!r}") from None


def pandas_logical_type(series: pd.Series) -> str:
    """The logical type of a pandas column, or "unknown" when no value says (empty, all null)."""
    dtype = series.dtype
    api = pd.api.types
    if api.is_bool_dtype(dtype):
        return "boolean"
    if api.is_integer_dtype(dtype):
        return "integer"
    if api.is_float_dtype(dtype):
        return "double" if dtype.itemsize >= 8 else "real"
    if isinstance(dtype, pd.DatetimeTZDtype):
        return "timestamptz"
    if api.is_datetime64_dtype(dtype):
        return "timestamp"  # naive: a real difference from timestamptz, not an unknown
    if isinstance(dtype, pd.StringDtype):
        return "text"
    if api.is_object_dtype(dtype):
        return _object_type(series)
    return str(dtype)


def _object_type(series: pd.Series) -> str:
    values = series.dropna()
    if values.empty:
        return "unknown"
    v = values.iloc[0]
    if isinstance(v, str):
        return "text"
    if isinstance(v, dt.datetime):  # before date: datetime subclasses date
        return "timestamptz" if v.tzinfo is not None else "timestamp"
    if isinstance(v, dt.date):
        return "date"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, float):
        return "double"
    if isinstance(v, Decimal):
        return "numeric"
    return type(v).__name__.lower()


def compare_columns(expected: dict[str, str], actual: dict[str, str]) -> list[str]:
    """Human-readable differences; an empty list means the schemas are equal.

    An actual type of "unknown" (nothing to infer it from) matches anything.
    """
    out = []
    for col, want in expected.items():
        if col not in actual:
            out.append(f"missing column {col}")
        elif actual[col] != "unknown" and actual[col] != want:
            out.append(f"column {col}: expected {want}, got {actual[col]}")
    out.extend(f"unexpected column {col}" for col in actual if col not in expected)
    return out
