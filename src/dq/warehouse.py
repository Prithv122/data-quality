"""Read access to a warehouse, in logical types, for DuckDB (fixture) and Postgres (real #22)."""

from __future__ import annotations

import warnings
from typing import Any, Protocol, Self

import duckdb
import pandas as pd

from dq.contract import logical_type

_COLUMNS_SQL = (
    "SELECT column_name, data_type FROM information_schema.columns "
    "WHERE table_schema = {p} AND table_name = {p} ORDER BY ordinal_position"
)


class Warehouse(Protocol):
    def fetch_df(self, sql: str) -> pd.DataFrame: ...

    def columns(self, table: str) -> dict[str, str]:
        """Column -> logical type for a table given as "schema.name"."""
        ...

    def close(self) -> None: ...


def _split(table: str) -> tuple[str, str]:
    schema, _, name = table.partition(".")
    if not name:
        raise ValueError(f"table must be 'schema.name', got {table!r}")
    return schema, name


class _Closing:
    def close(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class DuckDBWarehouse(_Closing):
    def __init__(self, path: str, read_only: bool = True) -> None:
        self._con = duckdb.connect(str(path), read_only=read_only)

    def fetch_df(self, sql: str) -> pd.DataFrame:
        with warnings.catch_warnings():
            # DuckDB's own conversion still passes a keyword pandas 3.1 deprecates.
            warnings.filterwarnings("ignore", message="The inplace keyword")
            return self._con.execute(sql).fetchdf()

    def columns(self, table: str) -> dict[str, str]:
        rows = self._con.execute(_COLUMNS_SQL.format(p="?"), list(_split(table))).fetchall()
        if not rows:
            raise KeyError(f"no such table {table}")
        return {name: logical_type(dtype) for name, dtype in rows}

    def close(self) -> None:
        self._con.close()


class PostgresWarehouse(_Closing):
    def __init__(self, dsn: str) -> None:
        import psycopg  # optional group `postgres`; imported here so the module loads without it

        self._con: Any = psycopg.connect(dsn, autocommit=True)

    def fetch_df(self, sql: str) -> pd.DataFrame:
        with self._con.cursor() as cur:
            cur.execute(sql)
            names = [d.name for d in cur.description or []]
            return pd.DataFrame(cur.fetchall(), columns=names)

    def columns(self, table: str) -> dict[str, str]:
        with self._con.cursor() as cur:
            cur.execute(_COLUMNS_SQL.format(p="%s"), list(_split(table)))
            rows = cur.fetchall()
        if not rows:
            raise KeyError(f"no such table {table}")
        return {name: logical_type(dtype) for name, dtype in rows}

    def close(self) -> None:
        self._con.close()
