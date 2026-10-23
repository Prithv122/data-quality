import datetime as dt
import math

import pandas as pd
import pytest

from dq import spec
from dq.checkdefs import load_checks
from dq.simulate import CLEAN_ID, MODES, contexts, run_grid, run_stream
from dq.types import CheckResult, MetricValue
from dq.world import WEATHER_COLUMNS, Stream, check_time, ingest_time, timestamps

SUITE = load_checks()
STATIC = [c for c in SUITE.checks if c.kind == "static"]
THRESHOLD = [c for c in SUITE.checks if c.kind == "threshold"]
BIG_BATCH = 18  # the batch the fake rules object to


# --- fakes: nothing here touches dq.checks ----------------------------------------------


def fake_static(defn, ctx, contract=None):
    n = len(ctx.batch_df)
    status = "fail" if n > 10 else "pass"
    return CheckResult(defn.id, defn.family, ctx.table, ctx.batch, status, float(n), "<= 10")


def fake_metric(defn, ctx):
    return MetricValue(float(len(ctx.batch_df)))


def fake_threshold_result(defn, table, batch, decision):
    return CheckResult(
        defn.id, defn.family, table, batch, decision.status, decision.observed, decision.expected
    )


FAKES = {
    "run_static": fake_static,
    "compute_metric": fake_metric,
    "threshold_result": fake_threshold_result,
}


def _frame(rows, batch):
    """rows: list of (city_id, date offset in days)."""
    n = len(rows)
    return pd.DataFrame(
        {
            "city_id": pd.Series([r[0] for r in rows], dtype="str"),
            "date": pd.Series(
                [spec.STREAM_START + dt.timedelta(days=r[1]) for r in rows], dtype="object"
            ),
            "temp_max_c": pd.Series([20.0] * n, dtype="float64"),
            "temp_min_c": pd.Series([10.0] * n, dtype="float64"),
            "precip_mm": pd.Series([0.0] * n, dtype="float64"),
            "wind_max_kmh": pd.Series([5.0] * n, dtype="float64"),
            "ingested_at": timestamps(ingest_time(batch), n),
        },
        columns=list(WEATHER_COLUMNS),
    )


def _stream(counts, cities=("a-01", "a-02")):
    batches = [
        _frame([(cities[i % len(cities)], b) for i in range(n)], b + 1)
        for b, n in enumerate(counts)
    ]
    city_rows = pd.DataFrame(
        {
            "city_id": pd.Series(list(cities), dtype="str"),
            "onboard_batch": [1, 3][: len(cities)],
        }
    )
    n = len(counts)
    return Stream(
        seed=1,
        world="W1",
        batches=batches,
        cities=city_rows,
        ingest_times=[ingest_time(b) for b in range(1, n + 1)],
        check_times=[check_time(b) for b in range(1, n + 1)],
    )


# --- contexts ----------------------------------------------------------------------------


def test_contexts_track_loaded_keys_and_freshness_through_an_empty_batch():
    stream = _stream([2, 0, 1])
    ctxs = contexts(stream)
    assert [c.batch for c in ctxs] == [1, 2, 3]
    assert all(c.table == "raw.weather_daily" for c in ctxs)

    day0, day2 = spec.STREAM_START, spec.STREAM_START + dt.timedelta(days=2)
    assert ctxs[0].loaded_keys == frozenset()
    assert ctxs[1].loaded_keys == frozenset({("a-01", day0), ("a-02", day0)})
    assert ctxs[2].loaded_keys == ctxs[1].loaded_keys  # the empty batch added nothing
    assert len(ctxs[1].batch_df) == 0

    # max ingested_at includes the batch itself and does not move through an empty batch
    assert ctxs[0].max_ingested_at == ingest_time(1)
    assert ctxs[1].max_ingested_at == ingest_time(1)
    assert ctxs[2].max_ingested_at == ingest_time(3)
    assert ctxs[2].check_time == check_time(3)
    assert ("a-01", day2) not in ctxs[2].loaded_keys  # this batch's own keys come later

    assert ctxs[0].parent_keys == frozenset({"a-01"})
    assert ctxs[2].parent_keys == frozenset({"a-01", "a-02"})


def test_contexts_with_nothing_loaded_have_no_freshness():
    stream = _stream([0, 0])
    assert [c.max_ingested_at for c in contexts(stream)] == [None, None]


def test_contexts_skip_rows_without_a_usable_key():
    stream = _stream([3])
    df = stream.batches[0].copy()
    df.loc[0, "city_id"] = pd.NA
    df.loc[1, "date"] = None
    stream.batches[0] = df
    stream.batches.append(_frame([("a-01", 5)], 2))
    stream.ingest_times.append(ingest_time(2))
    stream.check_times.append(check_time(2))
    ctxs = contexts(stream)
    assert len(ctxs[1].loaded_keys) == 1


def test_contexts_survive_a_missing_key_column():
    stream = _stream([2, 2])
    stream.batches[0] = stream.batches[0].drop(columns=["city_id"])
    ctxs = contexts(stream)
    assert ctxs[1].loaded_keys == frozenset()


# --- run_stream --------------------------------------------------------------------------


def _counts_with_spike(n=24):
    counts = [5] * n
    counts[BIG_BATCH - 1] = 50
    return counts


def test_run_stream_shape_modes_and_k_fan_out():
    ks = (2.0, 4.0, 8.0)
    n = 24
    out = run_stream(_stream(_counts_with_spike(n)), SUITE, ks, **FAKES)
    assert list(out.columns) == [
        "batch", "check", "family", "mode", "k", "status", "alarm", "observed",
    ]  # fmt: skip
    expect = len(STATIC) * n + len(THRESHOLD) * len(MODES) * len(ks) * n
    assert len(out) == expect

    static = out[out["mode"] == "static"]
    assert set(static["check"]) == {c.id for c in STATIC}
    assert static["k"].isna().all()
    assert (static.groupby("check").size() == n).all()

    thr = out[out["mode"] != "static"]
    assert set(thr["mode"]) == {"frozen", "rolling"}
    assert set(thr["k"]) == set(ks)
    assert set(thr["check"]) == {c.id for c in THRESHOLD}
    assert (thr.groupby(["check", "mode", "k"]).size() == n).all()


def test_run_stream_statuses_follow_the_fake_rules():
    ks = (2.0, 4.0)
    out = run_stream(_stream(_counts_with_spike()), SUITE, ks, **FAKES)

    static = out[out["mode"] == "static"]
    hit = static[static["alarm"]]
    assert set(hit["batch"]) == {BIG_BATCH}
    assert set(hit["status"]) == {"fail"}
    assert (static[static["batch"] == 3]["observed"] == 5.0).all()

    vol = out[(out["check"] == "volume.rows") & (out["mode"] != "static")]
    assert set(vol.loc[vol["batch"] <= spec.WARMUP_BATCHES, "status"]) == {"skipped"}
    alarms = vol[vol["alarm"]]
    assert set(alarms["batch"]) == {BIG_BATCH}
    assert len(alarms) == len(MODES) * len(ks)
    assert not out.loc[out["status"] == "skipped", "alarm"].any()


def test_run_stream_dtypes_are_stable_when_nothing_alarms():
    out = run_stream(_stream([5] * 20), SUITE, (4.0,), **FAKES)
    assert out["alarm"].dtype == bool
    assert out["k"].dtype == "float64" and out["observed"].dtype == "float64"
    assert not out["alarm"].any()


def test_run_stream_passes_the_contract_through():
    seen = []

    def spy(defn, ctx, contract=None):
        seen.append(contract)
        return fake_static(defn, ctx, contract)

    marker = object()
    run_stream(
        _stream([5, 5]),
        SUITE,
        (4.0,),
        run_static=spy,
        compute_metric=fake_metric,
        threshold_result=fake_threshold_result,
        contract=marker,
    )
    assert seen and all(c is marker for c in seen)


# --- run_grid ----------------------------------------------------------------------------


@pytest.fixture(scope="module")
def grid():
    return run_grid([3601], ["W0"], (4.0,), **FAKES)


def test_run_grid_covers_the_clean_stream_and_every_condition(grid):
    res = grid.results
    assert len(grid.conditions) == 24
    assert set(grid.conditions["id"]) == set(grid.verify_problems)
    assert all(p == [] for p in grid.verify_problems.values())
    assert set(res["stream"]) == {CLEAN_ID, *grid.conditions["id"]}
    assert res["stream"].nunique() == 25

    clean = res[res["stream"] == CLEAN_ID]
    assert (clean["fault"] == "none").all() and (clean["severity"] == "none").all()
    assert clean["onset"].isna().all()
    cond = res[res["stream"] == "W0-3601-null_spike-small"]
    row = grid.conditions.set_index("id").loc["W0-3601-null_spike-small"]
    assert (cond["onset"] == row["onset"]).all()
    assert (cond["column"] == row["column"]).all()
    assert list(res.columns[:7]) == [
        "world", "seed", "stream", "fault", "severity", "onset", "column",
    ]  # fmt: skip


def test_run_grid_order_is_deterministic(grid):
    streams = list(dict.fromkeys(grid.results["stream"]))
    assert streams[0] == CLEAN_ID
    assert streams[1:] == list(grid.conditions["id"])
    assert grid.results["batch"].between(1, spec.N_BATCHES).all()


def test_faults_show_up_in_the_fake_row_counts(grid):
    res = grid.results
    cond = grid.conditions.set_index("id").loc["W0-3601-volume-large"]
    rows = res[(res["stream"] == "W0-3601-volume-large") & (res["mode"] == "static")]
    clean = res[(res["stream"] == CLEAN_ID) & (res["mode"] == "static")]
    check = "unique.city_id_date"
    a = rows[rows["check"] == check].set_index("batch")["observed"]
    b = clean[clean["check"] == check].set_index("batch")["observed"]
    onset = int(cond["onset"])
    assert a[onset] > 1.5 * b[onset]  # the replay doubles the fake row count
    assert (a.loc[: onset - 1] == b.loc[: onset - 1]).all()  # nothing differs before the onset
    assert math.isnan(rows["k"].iloc[0])


@pytest.mark.slow
def test_parallel_run_gives_identical_results(grid):
    parallel = run_grid([3601], ["W0"], (4.0,), jobs=2, **FAKES)
    assert parallel.results.equals(grid.results)
    assert parallel.conditions.equals(grid.conditions)
    assert parallel.verify_problems == grid.verify_problems
