"""Schema family: the staging model still builds, and columns and types equal the contract.

Also home of the column helpers the other families share, because "is the column there" is a
schema question and every other check must answer it before reading data.
"""

from __future__ import annotations

import itertools

import duckdb
import pandas as pd

from dq import sql22
from dq.checkdefs import CheckDef
from dq.contract import Contract, compare_columns, load_contract, pandas_logical_type
from dq.types import BatchContext, Status

Outcome = tuple[Status, float | None, str, str]  # status, observed, expected, detail


def missing_detail(df: pd.DataFrame, columns: list[str]) -> str | None:
    """'column X missing' for each absent column, joined; None when all are present."""
    absent = [c for c in columns if c not in df.columns]
    if not absent:
        return None
    return "; ".join(f"column {c} missing" for c in absent)


def numeric_column(df: pd.DataFrame, column: str) -> tuple[pd.Series | None, str]:
    """The column as float64 (NaN for null), or (None, why) when it is not numeric."""
    s = df[column]
    if pd.api.types.is_bool_dtype(s.dtype) or not (
        pd.api.types.is_numeric_dtype(s.dtype) or pd.api.types.is_object_dtype(s.dtype)
    ):
        return None, f"column {column} is not numeric (dtype {s.dtype})"
    try:
        return pd.to_numeric(s).astype("float64"), ""
    except (ValueError, TypeError):
        return None, f"column {column} is not numeric (dtype {s.dtype})"


_ROOT: duckdb.DuckDBPyConnection | None = None
_SCRATCH_IDS = itertools.count()


def _scratch_connection() -> duckdb.DuckDBPyConnection:
    """A cursor on a shared in-memory instance. Opening a new instance costs ~55 ms on Windows
    (thread pool start-up); a cursor plus a fresh attached database costs ~10 ms and is just
    as isolated."""
    global _ROOT
    if _ROOT is None:
        _ROOT = duckdb.connect(":memory:")
    return _ROOT.cursor()


def staging_sql_runs(defn: CheckDef, ctx: BatchContext) -> Outcome:
    """Run #22's stg_weather_daily on the batch, as `dbt build` would. Failure = broken build."""
    expected = "stg_weather_daily builds on the batch"
    scratch = f"scratch_{next(_SCRATCH_IDS)}"
    cur = _scratch_connection()
    try:
        cur.execute(f"ATTACH ':memory:' AS {scratch}")
        try:
            cur.execute(f"USE {scratch}")
            cur.register("batch", ctx.batch_df)
            cur.execute("CREATE SCHEMA raw")
            cur.execute("CREATE TABLE raw.weather_daily AS SELECT * FROM batch")
            # Count in SQL: fetching TIMESTAMPTZ values into Python needs pytz, not a dependency.
            model = sql22.render("stg_weather_daily")
            rows = cur.execute(f"SELECT count(*) FROM (\n{model}\n)").fetchone()[0]
        finally:
            cur.execute("USE memory")
            cur.execute(f"DETACH {scratch}")
    except Exception as exc:  # any failure is what a red `dbt build` would be
        first = (str(exc).splitlines() or [type(exc).__name__])[0]
        return "fail", None, expected, first
    finally:
        cur.close()
    return "pass", float(rows), expected, ""


def contract_equal(defn: CheckDef, ctx: BatchContext, contract: Contract | None = None) -> Outcome:
    expected = "columns and types equal the contract"
    contract = contract or load_contract()
    table = contract.tables.get(ctx.table)
    if table is None:
        return "error", None, expected, f"table {ctx.table} not in the contract"
    actual = {str(c): pandas_logical_type(ctx.batch_df[c]) for c in ctx.batch_df.columns}
    diffs = compare_columns(table.columns, actual)
    if diffs:
        return "fail", float(len(diffs)), expected, "; ".join(diffs)
    return "pass", 0.0, expected, ""
