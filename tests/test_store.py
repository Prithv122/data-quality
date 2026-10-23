"""The results store: runs, results and every summary layout round-trip through SQLite."""

from __future__ import annotations

import sqlite3
import time

import numpy as np
import pandas as pd
import pytest

from dq import store, summary
from dq.types import CheckResult


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "results.sqlite"
    store.init(path)
    return path


def test_init_is_idempotent_and_creates_tables(db):
    store.init(db)
    with sqlite3.connect(db) as con:
        names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"runs", "check_results", "summaries"} <= names


def test_runs_round_trip(db):
    assert store.latest_run_id(db) is None
    a = store.write_run(db, "first", "smoke", {"seeds": [3601], "ks": [4.0]}, git_sha="abc1234")
    b = store.write_run(db, "second", "reported", {"worlds": ["W1", "W0"]})
    assert (a, b) == (1, 2)
    assert store.latest_run_id(db) == 2
    runs = store.read_runs(db)
    assert runs["label"].tolist() == ["first", "second"]
    assert runs["scale"].tolist() == ["smoke", "reported"]
    assert runs.loc[0, "git_sha"] == "abc1234"
    assert pd.isna(runs.loc[1, "git_sha"])
    assert runs.loc[0, "created_at"].endswith("Z")
    assert store.read_meta(db, a) == {"seeds": [3601], "ks": [4.0]}
    with pytest.raises(KeyError):
        store.read_meta(db, 99)


def test_results_round_trip_with_missing_values(db):
    run = store.write_run(db, "r", "smoke", {})
    df = pd.DataFrame(
        {
            "world": ["W1", "W1", None],
            "seed": [3601, 3601, None],
            "stream": ["fault:volume:small", "clean", None],
            "batch": [15, 16, None],
            "check_id": ["volume.rows", "volume.rows", "x"],
            "mode": ["rolling", None, "frozen"],
            "k": [4.0, np.nan, 2.5],
            "status": ["fail", "pass", "error"],
            "observed": [190.0, np.nan, None],
            "expected": ["<= 200", "", None],
        }
    )
    assert store.write_results(db, run, df, source="stream") == 3
    out = store.read_results(db, run)
    assert len(out) == 3
    assert out["source"].tolist() == ["stream"] * 3
    assert out["batch"].tolist()[:2] == [15, 16] and pd.isna(out["batch"][2])
    assert str(out["batch"].dtype) == "Int64"
    assert out["seed"].tolist()[0] == 3601 and pd.isna(out["seed"][2])
    assert out["k"].isna().tolist() == [False, True, False]
    assert out["observed"].isna().tolist() == [False, True, True]
    assert out["alarm"].tolist() == [1, 0, 1]  # derived from status
    assert pd.isna(out["mode"][1]) and out["mode"][0] == "rolling"
    assert out["expected"][1] == ""
    assert out["family"].isna().all()


def test_results_filter_by_source_and_run(db):
    r1 = store.write_run(db, "a", "smoke", {})
    r2 = store.write_run(db, "b", "smoke", {})
    row = pd.DataFrame({"check_id": ["c"], "status": ["pass"], "batch": [1]})
    store.write_results(db, r1, row, source="stream")
    store.write_results(db, r1, row, source="lake")
    store.write_results(db, r2, row, source="stream")
    assert len(store.read_results(db, r1)) == 2
    assert len(store.read_results(db, r1, source="lake")) == 1
    assert len(store.read_results(db, r2)) == 1
    assert store.read_results(db, r2, source="lake").empty


def test_results_reject_unknown_columns(db):
    run = store.write_run(db, "r", "smoke", {})
    with pytest.raises(ValueError, match="unknown"):
        store.write_results(db, run, pd.DataFrame({"nope": [1]}))


def test_results_from_check_results(db):
    run = store.write_run(db, "r", "smoke", {})
    results = [
        CheckResult("a", "nulls", "t", 3, "fail", 2.0, "== 0", "two"),
        CheckResult("b", "range", "t", None, "pass", None, "ok", snapshot_id=42),
        CheckResult("c", "volume", "t", 4, "skipped", None, "n/a"),
    ]
    frame = store.results_from_check_results(results, source="lake")
    store.write_results(db, run, frame, source="lake")
    out = store.read_results(db, run, source="lake")
    assert out["check_id"].tolist() == ["a", "b", "c"]
    assert out["alarm"].tolist() == [1, 0, 0]
    assert out["snapshot_id"].tolist()[1] == 42 and pd.isna(out["snapshot_id"][0])
    assert pd.isna(out["batch"][1]) and out["batch"][0] == 3
    assert out["observed"][0] == 2.0 and pd.isna(out["observed"][1])
    assert out["detail"].tolist() == ["two", "", ""]


def test_many_rows_are_written_in_chunks(db, monkeypatch):
    monkeypatch.setattr(store, "CHUNK_ROWS", 1_000)
    run = store.write_run(db, "big", "smoke", {})
    n = 25_000
    df = pd.DataFrame(
        {
            "world": "W1",
            "seed": 3601,
            "batch": np.arange(n) % 60 + 1,
            "check_id": "volume.rows",
            "status": np.where(np.arange(n) % 7 == 0, "fail", "pass"),
            "k": 4.0,
            "observed": np.arange(n, dtype="float64"),
        }
    )
    t0 = time.perf_counter()
    store.write_results(db, run, df)
    assert time.perf_counter() - t0 < 10
    out = store.read_results(db, run)
    assert len(out) == n
    assert int(out["alarm"].sum()) == int((df["status"] == "fail").sum())
    assert out["observed"].iloc[-1] == n - 1


def test_latest_stream_is_the_last_written_group(db):
    run = store.write_run(db, "r", "smoke", {})
    first = pd.DataFrame({"check_id": ["a", "b"], "batch": [1, 1], "status": ["pass", "pass"]})
    last = pd.DataFrame({"check_id": ["a"], "batch": [2], "status": ["fail"]})
    store.write_results(db, run, first.assign(stream="s1", seed=1))
    store.write_results(db, run, last.assign(stream="s2", seed=2))
    store.write_results(db, run, last.assign(stream="s3"), source="other")
    out = store.read_latest_stream(db, run)
    assert out["stream"].tolist() == ["s2"]
    assert store.read_latest_stream(db, run + 1).empty


def _tiny(name: str) -> pd.DataFrame:
    """One row per summary layout with a mix of text, ints, floats and a NaN."""
    cols = summary.SUMMARY_COLUMNS[name]
    text = {
        "world", "arm", "fault", "severity", "family", "check", "hypothesis", "verdict",
        "detail", "walk", "kind", "status", "expected", "predicted",
    }  # fmt: skip
    ints = {
        "detected", "n", "alarms", "batch_checks", "n_detected", "version", "snapshot_id",
        "seeds_detected",
    }  # fmt: skip
    rows = []
    for i in range(2):
        row = {}
        for c in cols:
            if c in text:
                row[c] = f"{c}-{i}"
            elif c in ints:
                row[c] = i + 3
            elif c == "reached_target":
                row[c] = bool(i)
            else:
                row[c] = np.nan if (c == "k" and i == 0) or (c == "ci_low" and i == 0) else 0.25 + i
        rows.append(row)
    return pd.DataFrame(rows)


@pytest.mark.parametrize("name", sorted(summary.SUMMARY_COLUMNS))
def test_summary_round_trip(db, name):
    run = store.write_run(db, "r", "smoke", {})
    df = _tiny(name)
    store.write_summary(db, run, name, df)
    out = store.read_summary(db, run, name)
    assert list(out.columns) == list(summary.SUMMARY_COLUMNS[name])
    assert len(out) == 2
    for col in df.columns:
        if pd.api.types.is_float_dtype(df[col]):
            assert out[col].isna().tolist() == df[col].isna().tolist(), col
            assert np.allclose(out[col].dropna(), df[col].dropna()), col
            assert pd.api.types.is_float_dtype(out[col]), col
        elif pd.api.types.is_integer_dtype(df[col]):
            assert out[col].tolist() == df[col].tolist(), col
            assert pd.api.types.is_integer_dtype(out[col]), col
        elif pd.api.types.is_bool_dtype(df[col]):
            assert out[col].tolist() == df[col].tolist(), col
        else:
            assert out[col].tolist() == df[col].tolist(), col


def test_summary_rewrite_replaces_and_names_are_listed(db):
    run = store.write_run(db, "r", "smoke", {})
    store.write_summary(db, run, "verdicts", _tiny("verdicts"))
    store.write_summary(db, run, "verdicts", _tiny("verdicts").iloc[:1])
    assert len(store.read_summary(db, run, "verdicts")) == 1
    assert store.summary_names(db, run) == ["verdicts"]


def test_summary_with_no_rows_keeps_its_columns(db):
    run = store.write_run(db, "r", "smoke", {})
    store.write_summary(db, run, "mismatches", summary.empty("mismatches"))
    out = store.read_summary(db, run, "mismatches")
    assert out.empty
    assert list(out.columns) == list(summary.SUMMARY_COLUMNS["mismatches"])


def test_summary_with_all_nan_column_comes_back_numeric(db):
    run = store.write_run(db, "r", "smoke", {})
    df = _tiny("operating_points").assign(k=np.nan)
    store.write_summary(db, run, "operating_points", df)
    out = store.read_summary(db, run, "operating_points")
    assert out["k"].isna().all() and pd.api.types.is_float_dtype(out["k"])


def test_write_summary_rejects_a_missing_column(db):
    run = store.write_run(db, "r", "smoke", {})
    bad = _tiny("recall").drop(columns=["ci_high"])
    with pytest.raises(ValueError, match="ci_high"):
        store.write_summary(db, run, "recall", bad)
    assert store.summary_names(db, run) == []


def test_read_summary_that_was_never_written(db):
    run = store.write_run(db, "r", "smoke", {})
    with pytest.raises(KeyError):
        store.read_summary(db, run, "recall")
