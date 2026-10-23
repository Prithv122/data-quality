"""Results database (SQLite): runs, per-check results and summary tables.

Plain stdlib `sqlite3`. A summary table is stored as JSON records, validated against the
column layout in `summary.py` on write, so the dashboard and the report read what evaluation
wrote. `NaN` is stored as NULL and comes back as `NaN`.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import pandas as pd

from dq import summary
from dq.types import ALARM_STATUSES, CheckResult

CHUNK_ROWS = 50_000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    label TEXT,
    scale TEXT,
    git_sha TEXT,
    meta_json TEXT
);
CREATE TABLE IF NOT EXISTS check_results (
    run_id INTEGER NOT NULL,
    source TEXT,
    world TEXT,
    seed INTEGER,
    stream TEXT,
    batch INTEGER,
    snapshot_id INTEGER,
    check_id TEXT,
    family TEXT,
    mode TEXT,
    k REAL,
    status TEXT,
    alarm INTEGER,
    observed REAL,
    expected TEXT,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS ix_results_run_source ON check_results (run_id, source);
CREATE TABLE IF NOT EXISTS summaries (
    run_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    data_json TEXT NOT NULL,
    PRIMARY KEY (run_id, name)
);
"""

# column -> storage kind, in table order (run_id is added by the writer)
RESULT_COLUMNS: dict[str, str] = {
    "source": "text",
    "world": "text",
    "seed": "int",
    "stream": "text",
    "batch": "int",
    "snapshot_id": "int",
    "check_id": "text",
    "family": "text",
    "mode": "text",
    "k": "real",
    "status": "text",
    "alarm": "int",
    "observed": "real",
    "expected": "text",
    "detail": "text",
}


@contextmanager
def _connect(path: str | Path) -> Iterator[sqlite3.Connection]:
    con = sqlite3.connect(str(path))
    try:
        con.executescript(_SCHEMA)
        yield con
        con.commit()
    finally:
        con.close()


def init(path: str | Path) -> Path:
    """Create the database file and tables if they are not there yet."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(path):
        pass
    return path


def write_run(
    path: str | Path,
    label: str,
    scale: str,
    meta: dict,
    git_sha: str | None = None,
) -> int:
    init(path)
    created = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    with _connect(path) as con:
        cur = con.execute(
            "INSERT INTO runs (created_at, label, scale, git_sha, meta_json) VALUES (?,?,?,?,?)",
            (created, label, scale, git_sha, json.dumps(meta, sort_keys=True, default=str)),
        )
        return int(cur.lastrowid)


def _column_values(series: pd.Series, kind: str) -> list:
    """Python values for one column, with every kind of missing value turned into None."""
    present = series.notna()
    if kind == "int":
        vals = series.astype("Int64").astype(object)
    elif kind == "real":
        vals = pd.to_numeric(series, errors="coerce").astype("float64").astype(object)
    else:
        vals = series.astype(object)
    return vals.where(present, None).tolist()


def write_results(
    path: str | Path, run_id: int, df: pd.DataFrame, source: str | None = "stream"
) -> int:
    """Insert rows; `df` may hold any subset of the `check_results` columns.

    Missing columns are NULL. `source` fills the column when `df` has none. `alarm` is derived
    from `status` when only `status` is given. Written in chunks so a million rows stay cheap.
    """
    init(path)
    unknown = [c for c in df.columns if c not in RESULT_COLUMNS]
    if unknown:
        raise ValueError(f"unknown check_results columns {unknown}")
    frame = df.reset_index(drop=True)
    if "source" not in frame.columns:
        frame = frame.assign(source=source)
    if "alarm" not in frame.columns and "status" in frame.columns:
        frame = frame.assign(alarm=frame["status"].isin(ALARM_STATUSES).astype(int))
    if "alarm" in frame.columns:
        frame = frame.assign(alarm=frame["alarm"].astype("float64").round())
    cols = list(RESULT_COLUMNS)
    placeholders = ",".join("?" * (len(cols) + 1))
    sql = f"INSERT INTO check_results (run_id, {', '.join(cols)}) VALUES ({placeholders})"
    total = 0
    with _connect(path) as con:
        for start in range(0, len(frame), CHUNK_ROWS):
            chunk = frame.iloc[start : start + CHUNK_ROWS]
            columns = [
                _column_values(chunk[c], kind) if c in chunk.columns else [None] * len(chunk)
                for c, kind in RESULT_COLUMNS.items()
            ]
            rows = zip([run_id] * len(chunk), *columns, strict=True)
            con.executemany(sql, rows)
            total += len(chunk)
    return total


def results_from_check_results(results: list[CheckResult], source: str) -> pd.DataFrame:
    """Own-code results as a frame ready for `write_results`."""
    return pd.DataFrame(
        {
            "source": [source] * len(results),
            "batch": pd.Series([r.batch for r in results], dtype="Int64"),
            "snapshot_id": pd.Series([r.snapshot_id for r in results], dtype="Int64"),
            "check_id": [r.check for r in results],
            "family": [r.family for r in results],
            "status": [r.status for r in results],
            "alarm": [int(r.alarm) for r in results],
            "observed": pd.Series([r.observed for r in results], dtype="float64"),
            "expected": [r.expected for r in results],
            "detail": [r.detail for r in results],
        }
    )


def _typed(df: pd.DataFrame) -> pd.DataFrame:
    for col, kind in RESULT_COLUMNS.items():
        if col not in df.columns:
            continue
        if kind == "int" and col != "alarm":
            df[col] = df[col].astype("Int64")
        elif kind == "real":
            df[col] = df[col].astype("float64")
        elif kind == "text":
            df[col] = df[col].astype("str")
    return df


def read_runs(path: str | Path) -> pd.DataFrame:
    init(path)
    with _connect(path) as con:
        df = pd.read_sql_query("SELECT * FROM runs ORDER BY run_id", con)
    for col in ("created_at", "label", "scale", "git_sha", "meta_json"):
        df[col] = df[col].astype("str")
    return df


def read_meta(path: str | Path, run_id: int) -> dict:
    with _connect(path) as con:
        row = con.execute("SELECT meta_json FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        raise KeyError(f"no run {run_id}")
    return json.loads(row[0]) if row[0] else {}


def latest_run_id(path: str | Path) -> int | None:
    init(path)
    with _connect(path) as con:
        row = con.execute("SELECT MAX(run_id) FROM runs").fetchone()
    return None if row[0] is None else int(row[0])


def read_results(path: str | Path, run_id: int, source: str | None = None) -> pd.DataFrame:
    init(path)
    sql = "SELECT * FROM check_results WHERE run_id = ?"
    args: list = [run_id]
    if source is not None:
        sql += " AND source = ?"
        args.append(source)
    with _connect(path) as con:
        df = pd.read_sql_query(sql + " ORDER BY rowid", con, params=args)
    df["alarm"] = df["alarm"].fillna(0).astype("int64")
    return _typed(df)


def read_latest_stream(path: str | Path, run_id: int) -> pd.DataFrame:
    """Rows of the most recently written stream (or lake walk): source 'stream' or 'lake'."""
    init(path)
    with _connect(path) as con:
        key = con.execute(
            "SELECT source, world, seed, stream FROM check_results "
            "WHERE run_id = ? AND source IN ('stream', 'lake') ORDER BY rowid DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        if key is None:
            return _typed(pd.read_sql_query("SELECT * FROM check_results WHERE 0", con))
        df = pd.read_sql_query(
            "SELECT * FROM check_results WHERE run_id = ? AND source IS ? AND world IS ? "
            "AND seed IS ? AND stream IS ? ORDER BY rowid",
            con,
            params=[run_id, *key],
        )
    df["alarm"] = df["alarm"].fillna(0).astype("int64")
    return _typed(df)


def write_summary(path: str | Path, run_id: int, name: str, df: pd.DataFrame) -> None:
    """Store one summary table; raises ValueError when a declared column is missing."""
    checked = summary.validate(name, df)
    payload = checked.to_json(orient="records", date_format="iso")
    init(path)
    with _connect(path) as con:
        con.execute("DELETE FROM summaries WHERE run_id = ? AND name = ?", (run_id, name))
        con.execute(
            "INSERT INTO summaries (run_id, name, data_json) VALUES (?,?,?)",
            (run_id, name, payload),
        )


def summary_names(path: str | Path, run_id: int) -> list[str]:
    init(path)
    with _connect(path) as con:
        rows = con.execute(
            "SELECT name FROM summaries WHERE run_id = ? ORDER BY name", (run_id,)
        ).fetchall()
    return [r[0] for r in rows]


def read_summary(path: str | Path, run_id: int, name: str) -> pd.DataFrame:
    """The stored table with its declared columns; `KeyError` if it was never written."""
    init(path)
    with _connect(path) as con:
        row = con.execute(
            "SELECT data_json FROM summaries WHERE run_id = ? AND name = ?", (run_id, name)
        ).fetchone()
    if row is None:
        raise KeyError(f"run {run_id} has no summary {name!r}")
    records = json.loads(row[0])
    cols = list(summary.SUMMARY_COLUMNS[name])
    df = pd.DataFrame(records, columns=cols)
    for col in cols:
        values = df[col]
        non_null = [v for v in values.tolist() if v is not None and v == v]
        if not non_null:
            df[col] = np.full(len(df), np.nan)
        elif all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in non_null):
            df[col] = pd.to_numeric(values, errors="coerce")
        elif all(isinstance(v, bool) for v in non_null):
            df[col] = values.astype("boolean") if values.isna().any() else values.astype(bool)
    return df
