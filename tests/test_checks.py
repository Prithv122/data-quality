import dataclasses
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from dq import adaptive, checks, fixture
from dq.checkdefs import CheckDef, load_checks
from dq.contract import Contract, load_contract
from dq.types import BatchContext

SUITE = load_checks()
DAY = dt.date(2024, 1, 1)
CITIES = fixture.seed_cities()
CITY_IDS = frozenset(CITIES["city_id"])


def clean_batch() -> pd.DataFrame:
    return fixture.generate_weather(CITIES, DAY, DAY, seed=5)


def make_ctx(df: pd.DataFrame, **overrides) -> BatchContext:
    """A context for one day's batch: lands 02:00 the next day, checked at 06:00."""
    ingested = pd.Timestamp("2024-01-02 02:00", tz="UTC").to_pydatetime()
    base = BatchContext(
        table="raw.weather_daily",
        batch=3,
        batch_df=df,
        loaded_keys=frozenset(),
        parent_keys=CITY_IDS,
        max_ingested_at=ingested,
        check_time=dt.datetime(2024, 1, 2, 6, tzinfo=dt.UTC),
    )
    return dataclasses.replace(base, **overrides)


def run(check_id: str, ctx: BatchContext, contract: Contract | None = None):
    return checks.run_static(SUITE.by_id(check_id), ctx, contract)


STATIC_IDS = [c.id for c in SUITE.checks if c.kind == "static"]


def test_the_batch_frame_has_the_documented_dtypes():
    df = clean_batch()
    assert len(df) == 10
    assert df["temp_max_c"].dtype == "float64"
    assert all(type(d) is dt.date for d in df["date"])
    assert str(df["ingested_at"].dtype) == "datetime64[us, UTC]"


@pytest.mark.parametrize("check_id", STATIC_IDS)
def test_every_static_rule_passes_on_a_clean_batch(check_id):
    r = run(check_id, make_ctx(clean_batch()))
    assert r.status == "pass", r
    assert r.check == check_id
    assert (r.table, r.batch) == ("raw.weather_daily", 3)
    assert r.family == SUITE.by_id(check_id).family
    assert r.expected


def test_clean_batch_metrics():
    ctx = make_ctx(clean_batch())
    assert checks.compute_metric(SUITE.by_id("volume.rows"), ctx).value == 10
    assert checks.compute_metric(SUITE.by_id("null_rate.precip_mm"), ctx).value == 0
    assert checks.compute_metric(SUITE.by_id("freshness.lag"), ctx).value == pytest.approx(4.0)


def test_expected_strings_state_the_rule():
    ctx = make_ctx(clean_batch())
    assert run("not_null.city_id", ctx).expected == "no nulls"
    assert run("accepted_range.temp_max_c", ctx).expected == "within [-90, 60]"
    assert run("diurnal_non_negative", ctx).expected == "temp_max_c >= temp_min_c"
    assert run("freshness.source_22", ctx).expected == "<= 72 h (warn), <= 336 h (error)"


# --- schema ------------------------------------------------------------------------------


def test_dropped_column_breaks_everything_that_reads_it():
    df = clean_batch().drop(columns="temp_max_c")
    ctx = make_ctx(df)
    build = run("build.stg_weather_daily", ctx)
    assert build.status == "fail"
    assert "temp_max_c" in build.detail
    contract = run("schema.contract", ctx)
    assert contract.status == "fail"
    assert contract.observed == 1
    assert "missing column temp_max_c" in contract.detail
    for check_id in ("accepted_range.temp_max_c", "diurnal_non_negative"):
        r = run(check_id, ctx)
        assert (r.status, r.observed, r.detail) == ("error", None, "column temp_max_c missing")
    metric = checks.compute_metric(SUITE.by_id("null_rate.temp_max_c"), ctx)
    assert (metric.status, metric.value, metric.detail) == (
        "error",
        None,
        "column temp_max_c missing",
    )
    # untouched columns still pass
    assert run("accepted_range.temp_min_c", ctx).status == "pass"
    assert run("not_null.city_id", ctx).status == "pass"


def test_renamed_column_fails_build_and_contract_and_errors_the_range_check():
    df = clean_batch().rename(columns={"temp_max_c": "tmax_c"})
    ctx = make_ctx(df)
    assert run("build.stg_weather_daily", ctx).status == "fail"
    contract = run("schema.contract", ctx)
    assert contract.status == "fail"
    assert contract.observed == 2  # one missing, one unexpected
    assert "unexpected column tmax_c" in contract.detail
    assert run("accepted_range.temp_max_c", ctx).status == "error"


def test_float32_retype_fails_the_contract_but_the_build_passes():
    df = clean_batch().astype({"temp_max_c": "float32"})
    ctx = make_ctx(df)
    assert run("build.stg_weather_daily", ctx).status == "pass"
    contract = run("schema.contract", ctx)
    assert contract.status == "fail"
    assert "column temp_max_c: expected double, got real" in contract.detail
    assert run("accepted_range.temp_max_c", ctx).status == "pass"


def test_contract_uses_the_table_in_the_context_and_an_explicit_contract():
    ctx = make_ctx(clean_batch())
    assert run("schema.contract", ctx, load_contract()).status == "pass"
    other = dataclasses.replace(ctx, table="raw.unheard_of")
    r = run("schema.contract", other)
    assert r.status == "error"


def test_staging_build_returns_the_row_count_and_dedupes():
    df = clean_batch()
    doubled = pd.concat([df, df.iloc[:2]], ignore_index=True)
    r = run("build.stg_weather_daily", make_ctx(doubled))
    assert r.status == "pass"
    assert r.observed == 10  # stg_weather_daily keeps one row per (city_id, date)


def test_staging_build_failure_detail_is_one_line():
    r = run("build.stg_weather_daily", make_ctx(clean_batch().drop(columns="date")))
    assert r.status == "fail"
    assert "\n" not in r.detail


# --- freshness ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("hours", "status"),
    [(4, "pass"), (72, "pass"), (73, "warn"), (336, "warn"), (337, "fail")],
)
def test_freshness_boundaries(hours, status):
    check_time = dt.datetime(2024, 1, 2, 6, tzinfo=dt.UTC)
    ctx = make_ctx(clean_batch(), max_ingested_at=check_time - dt.timedelta(hours=hours))
    r = run("freshness.source_22", ctx)
    assert r.status == status
    assert r.observed == pytest.approx(hours)
    assert checks.compute_metric(SUITE.by_id("freshness.lag"), ctx).value == pytest.approx(hours)


def test_nothing_loaded_is_an_error_not_a_crash():
    ctx = make_ctx(clean_batch(), max_ingested_at=None)
    r = run("freshness.source_22", ctx)
    assert (r.status, r.observed, r.detail) == ("error", None, "nothing loaded")
    m = checks.compute_metric(SUITE.by_id("freshness.lag"), ctx)
    assert (m.status, m.value, m.detail) == ("error", None, "nothing loaded")


def test_naive_timestamps_are_read_as_utc():
    ctx = make_ctx(
        clean_batch(),
        max_ingested_at=dt.datetime(2024, 1, 2, 2),
        check_time=dt.datetime(2024, 1, 2, 6),
    )
    assert run("freshness.source_22", ctx).observed == pytest.approx(4.0)


# --- nulls -------------------------------------------------------------------------------


def test_one_null_city_id():
    df = clean_batch()
    df.loc[0, "city_id"] = None
    ctx = make_ctx(df)
    r = run("not_null.city_id", ctx)
    assert (r.status, r.observed) == ("fail", 1)
    assert run("not_null.date", ctx).status == "pass"


def test_null_date_and_null_rate():
    df = clean_batch()
    df["date"] = pd.Series([DAY] * 10, dtype=object)
    df.loc[[1, 2], "date"] = None
    df.loc[[0, 1, 2, 3, 4], "precip_mm"] = np.nan
    ctx = make_ctx(df)
    assert run("not_null.date", ctx).observed == 2
    assert checks.compute_metric(SUITE.by_id("null_rate.precip_mm"), ctx).value == 0.5
    # nulls in a measure column are not a not_null failure: only city_id and date are tested
    assert run("accepted_range.temp_max_c", make_ctx(df)).status == "pass"


def test_null_rate_of_a_missing_column_and_of_an_empty_batch():
    m = checks.compute_metric(
        SUITE.by_id("null_rate.temp_max_c"), make_ctx(clean_batch().drop(columns="temp_max_c"))
    )
    assert (m.status, m.detail) == ("error", "column temp_max_c missing")
    empty = checks.compute_metric(
        SUITE.by_id("null_rate.temp_max_c"), make_ctx(clean_batch().iloc[0:0])
    )
    assert (empty.status, empty.value, empty.detail) == ("skipped", None, "empty batch")


# --- unique ------------------------------------------------------------------------------


def test_duplicate_row_within_the_batch():
    df = clean_batch()
    df = pd.concat([df, df.iloc[[0]]], ignore_index=True)
    r = run("unique.city_id_date", make_ctx(df))
    assert (r.status, r.observed) == ("fail", 1)


def test_every_extra_copy_counts():
    df = clean_batch()
    df = pd.concat([df, df.iloc[[0]], df.iloc[[0]], df.iloc[[1]]], ignore_index=True)
    assert run("unique.city_id_date", make_ctx(df)).observed == 3


def test_key_already_loaded():
    df = clean_batch()
    loaded = frozenset({("london", DAY), ("oslo", DAY), ("london", dt.date(1999, 1, 1))})
    r = run("unique.city_id_date", make_ctx(df, loaded_keys=loaded))
    assert (r.status, r.observed) == ("fail", 2)


def test_a_replayed_batch_hits_every_row():
    df = clean_batch()
    loaded = frozenset(zip(df["city_id"], df["date"], strict=True))
    assert run("unique.city_id_date", make_ctx(df, loaded_keys=loaded)).observed == 10


def test_row_that_is_both_repeated_and_loaded_counts_once():
    df = clean_batch()
    df = pd.concat([df, df.iloc[[0]]], ignore_index=True)
    loaded = frozenset({(df.loc[0, "city_id"], DAY)})
    assert run("unique.city_id_date", make_ctx(df, loaded_keys=loaded)).observed == 2


def test_null_key_parts_are_ignored_by_unique():
    df = clean_batch()
    df.loc[[0, 1], "city_id"] = None  # two rows with a null key part
    assert run("unique.city_id_date", make_ctx(df)).status == "pass"


def test_timestamp_dates_still_match_loaded_keys():
    df = clean_batch()
    df["date"] = pd.to_datetime(df["date"])
    loaded = frozenset({("london", DAY)})
    assert run("unique.city_id_date", make_ctx(df, loaded_keys=loaded)).observed == 1


def test_unique_with_a_missing_key_column_errors():
    r = run("unique.city_id_date", make_ctx(clean_batch().drop(columns="date")))
    assert (r.status, r.observed, r.detail) == ("error", None, "column date missing")


# --- range -------------------------------------------------------------------------------


def test_temp_max_200_fails_the_range_check_and_only_that():
    df = clean_batch()
    df.loc[3, "temp_max_c"] = 200.0
    ctx = make_ctx(df)
    r = run("accepted_range.temp_max_c", ctx)
    assert (r.status, r.observed) == ("fail", 1)
    assert run("accepted_range.temp_min_c", ctx).status == "pass"


def test_range_bounds_are_inclusive_and_nulls_pass():
    df = clean_batch()
    df.loc[0, "temp_max_c"] = 60.0
    df.loc[1, "temp_max_c"] = -90.0
    df.loc[2, "temp_max_c"] = np.nan
    assert run("accepted_range.temp_max_c", make_ctx(df)).status == "pass"
    df.loc[0, "temp_max_c"] = 60.1
    df.loc[1, "temp_max_c"] = -90.1
    assert run("accepted_range.temp_max_c", make_ctx(df)).observed == 2


def test_min_above_max_swap_is_in_range_but_fails_the_pair_check():
    df = clean_batch()
    a, b = df.loc[0, "temp_max_c"], df.loc[0, "temp_min_c"]
    df.loc[0, "temp_max_c"], df.loc[0, "temp_min_c"] = b, a
    assert a > b  # the generator's diurnal range is at least 0.5
    ctx = make_ctx(df)
    pair = run("diurnal_non_negative", ctx)
    assert (pair.status, pair.observed) == ("fail", 1)
    assert run("accepted_range.temp_max_c", ctx).status == "pass"
    assert run("accepted_range.temp_min_c", ctx).status == "pass"


def test_pair_check_ignores_rows_with_a_null_side():
    df = clean_batch()
    df.loc[0, "temp_max_c"] = np.nan
    df.loc[1, "temp_min_c"] = np.nan
    assert run("diurnal_non_negative", make_ctx(df)).status == "pass"


def test_equal_min_and_max_pass_the_pair_check():
    df = clean_batch()
    df["temp_min_c"] = df["temp_max_c"]
    assert run("diurnal_non_negative", make_ctx(df)).status == "pass"


def test_non_numeric_column_is_an_error_not_an_exception():
    df = clean_batch()
    df["temp_max_c"] = df["temp_max_c"].astype(str)
    ctx = make_ctx(df)
    for check_id in ("accepted_range.temp_max_c", "diurnal_non_negative"):
        r = run(check_id, ctx)
        assert r.status == "error", check_id
        assert r.observed is None
        assert "not numeric" in r.detail


def test_object_column_holding_numbers_is_compared_numerically():
    df = clean_batch()
    df["temp_max_c"] = pd.Series(df["temp_max_c"].tolist(), dtype=object)
    df.loc[0, "temp_max_c"] = 200.0
    assert run("accepted_range.temp_max_c", make_ctx(df)).observed == 1


# --- referential -------------------------------------------------------------------------


def test_unknown_city_id():
    df = clean_batch()
    df.loc[[0, 1], "city_id"] = ["atlantis", "atlantis"]
    r = run("relationships.city_id", make_ctx(df))
    assert (r.status, r.observed) == ("fail", 2)


def test_null_city_id_is_not_an_orphan():
    df = clean_batch()
    df.loc[0, "city_id"] = None
    assert run("relationships.city_id", make_ctx(df)).status == "pass"


def test_station_row_arriving_before_its_city_fails_until_the_parent_lands():
    df = clean_batch()
    df.loc[0, "city_id"] = "oslo-19"
    assert run("relationships.city_id", make_ctx(df)).status == "fail"
    ok = make_ctx(df, parent_keys=CITY_IDS | {"oslo-19"})
    assert run("relationships.city_id", ok).status == "pass"


# --- empty frame -------------------------------------------------------------------------


def test_empty_frame_with_the_right_columns():
    ctx = make_ctx(clean_batch().iloc[0:0].reset_index(drop=True))
    assert run("build.stg_weather_daily", ctx).status == "pass"
    assert run("build.stg_weather_daily", ctx).observed == 0
    assert run("schema.contract", ctx).status == "pass"  # empty keeps its dtypes
    for check_id in STATIC_IDS:
        if check_id.startswith("freshness"):
            continue
        assert run(check_id, ctx).status == "pass", check_id
    assert checks.compute_metric(SUITE.by_id("volume.rows"), ctx).value == 0
    assert checks.compute_metric(SUITE.by_id("null_rate.temp_max_c"), ctx).status == "skipped"


def test_empty_object_columns_do_not_trip_the_contract():
    ctx = make_ctx(pd.DataFrame({c: pd.Series([], dtype=object) for c in clean_batch().columns}))
    assert run("schema.contract", ctx).status == "pass"  # "unknown" matches anything
    assert run("build.stg_weather_daily", ctx).status == "pass"


def test_frame_with_no_columns_at_all_never_raises():
    ctx = make_ctx(pd.DataFrame())
    for defn in SUITE.checks:
        if defn.kind == "static":
            r = checks.run_static(defn, ctx)
            assert r.status in {"fail", "error", "pass"}, defn.id
        else:
            checks.compute_metric(defn, ctx)
    assert run("schema.contract", ctx).status == "fail"
    assert run("build.stg_weather_daily", ctx).status == "fail"


# --- dispatch ----------------------------------------------------------------------------


def test_run_static_rejects_a_threshold_check_or_unknown_rule():
    with pytest.raises(ValueError):
        checks.run_static(SUITE.by_id("volume.rows"), make_ctx(clean_batch()))
    bogus = CheckDef(
        id="x", family="nulls", kind="static", arms=frozenset({"A"}), rule="no_such_rule"
    )
    with pytest.raises(ValueError):
        checks.run_static(bogus, make_ctx(clean_batch()))


def test_compute_metric_rejects_an_unknown_metric():
    bogus = CheckDef(id="x", family="volume", kind="threshold", arms=frozenset(), metric="nope")
    with pytest.raises(ValueError):
        checks.compute_metric(bogus, make_ctx(clean_batch()))


def test_threshold_result_copies_the_decision():
    defn = SUITE.by_id("volume.rows")
    decision = adaptive.Decision(
        status="fail", observed=5.0, median=10.0, scale=1.0, expected="in [6, 14]", detail="low"
    )
    r = checks.threshold_result(defn, "raw.weather_daily", 17, decision)
    assert (r.check, r.family, r.table, r.batch) == (
        "volume.rows",
        "volume",
        "raw.weather_daily",
        17,
    )
    assert (r.status, r.observed, r.expected, r.detail) == ("fail", 5.0, "in [6, 14]", "low")
    assert r.alarm
