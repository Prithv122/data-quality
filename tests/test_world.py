import datetime as dt

import pandas as pd
import pytest

from dq import spec
from dq.world import WEATHER_COLUMNS, generate_stream


@pytest.fixture(scope="module")
def w1():
    return generate_stream(3601, "W1")


@pytest.fixture(scope="module")
def w0():
    return generate_stream(3601, "W0")


def _all_rows(stream):
    return pd.concat(stream.batches, ignore_index=True)


def test_same_seed_and_world_give_the_same_stream(w1):
    again = generate_stream(3601, "W1")
    assert len(again.batches) == len(w1.batches)
    assert all(a.equals(b) for a, b in zip(again.batches, w1.batches, strict=True))
    assert again.cities.equals(w1.cities)


def test_other_seed_gives_other_data(w1):
    other = generate_stream(3602, "W1")
    assert not other.batches[20].equals(w1.batches[20])


def test_unknown_world_is_rejected():
    with pytest.raises(ValueError):
        generate_stream(1, "W9")


def test_dtypes_and_column_order(w1):
    for df in w1.batches:
        assert list(df.columns) == list(WEATHER_COLUMNS)
        assert str(df["city_id"].dtype) == "str"
        assert df["temp_max_c"].dtype == "float64"
        assert df["temp_min_c"].dtype == "float64"
        assert df["precip_mm"].dtype == "float64"
        assert df["wind_max_kmh"].dtype == "float64"
        assert str(df["ingested_at"].dtype) == "datetime64[us, UTC]"
        assert all(isinstance(d, dt.date) and not isinstance(d, dt.datetime) for d in df["date"])
    cities = w1.cities
    assert str(cities["loaded_at"].dtype) == "datetime64[us, UTC]"
    assert cities["population"].dtype == "int64"


def test_sixty_batches_with_the_documented_times(w1):
    assert len(w1.batches) == spec.N_BATCHES
    assert w1.ingest_times[0] == dt.datetime(2024, 1, 2, 2, 0, tzinfo=dt.UTC)
    assert w1.check_times[0] == dt.datetime(2024, 1, 2, 6, 0, tzinfo=dt.UTC)
    assert w1.ingest_times[59] == dt.datetime(2024, 3, 1, 2, 0, tzinfo=dt.UTC)
    # an on-time row's ingested_at is its date + 1 day 02:00; a late row carries the landing batch's
    for b, df in enumerate(w1.batches, start=1):
        assert (df["ingested_at"] == w1.ingest_times[b - 1]).all()
        day = spec.STREAM_START + dt.timedelta(days=b - 1)
        assert all(d in (day, day - dt.timedelta(days=1)) for d in df["date"])


def test_station_counts_grow_only_in_w1(w1, w0):
    assert len(w1.parent_keys(1)) == 180
    assert len(w1.parent_keys(3)) == 180
    assert len(w1.parent_keys(4)) == 181
    assert len(w1.parent_keys(60)) == 195
    assert len(w0.parent_keys(60)) == 180
    onboard = w1.cities.loc[w1.cities["onboard_batch"] > 1, "onboard_batch"].tolist()
    assert onboard == list(range(4, 61, 4))
    assert w1.cities["city_id"].is_unique
    assert w1.cities["city_id"].iloc[0] == "chennai-01"
    # round-robin over the ten cities, new station numbers 19 and 20
    new_ids = w1.cities.loc[w1.cities["onboard_batch"] > 1, "city_id"].tolist()
    assert new_ids[0] == "chennai-19" and new_ids[1] == "mumbai-19" and new_ids[10] == "chennai-20"


def test_w0_equals_w1_except_for_onboarded_stations(w1, w0):
    onboarded = set(w1.cities.loc[w1.cities["onboard_batch"] > 1, "city_id"])
    for b in range(spec.N_BATCHES):
        w1_old = w1.batches[b][~w1.batches[b]["city_id"].isin(onboarded)]
        assert w1_old.reset_index(drop=True).equals(w0.batches[b])
    new_rows = _all_rows(w1)["city_id"].isin(onboarded).sum()
    assert new_rows > 0


def test_onboarded_station_has_data_from_its_batch_only(w1):
    rows = _all_rows(w1)
    first = rows.groupby("city_id")["date"].min()
    newcomers = w1.cities[w1.cities["onboard_batch"] > 1]
    for r in newcomers.itertuples():
        start = spec.STREAM_START + dt.timedelta(days=r.onboard_batch - 1)
        assert first[r.city_id] >= start


def test_temperatures_never_cross(w1):
    rows = _all_rows(w1).dropna(subset=["temp_max_c", "temp_min_c"])
    assert (rows["temp_min_c"] <= rows["temp_max_c"]).all()
    assert rows["temp_max_c"].between(*spec.TEMP_CLAMP).all()


def test_every_row_points_at_a_registered_station(w1):
    for b, df in enumerate(w1.batches, start=1):
        assert set(df["city_id"]) <= w1.parent_keys(b)


def test_station_day_is_delivered_at_most_once(w1):
    rows = _all_rows(w1)
    assert not rows.duplicated(["city_id", "date"]).any()


def test_null_loss_and_lateness_rates_are_near_their_parameters(w1):
    rows = _all_rows(w1)
    assert 0.0015 < rows["temp_max_c"].isna().mean() < 0.012
    assert 0.0015 < rows["temp_min_c"].isna().mean() < 0.012
    assert 0.010 < rows["precip_mm"].isna().mean() < 0.035
    assert 0.003 < rows["wind_max_kmh"].isna().mean() < 0.02

    station_days = sum((spec.N_BATCHES - int(r.onboard_batch) + 1) for r in w1.cities.itertuples())
    delivered = len(rows)
    assert 0.93 < delivered / station_days < 0.995  # 2% lost, a few late rows dropped at the end

    late = 0
    for b, df in enumerate(w1.batches, start=1):
        day = spec.STREAM_START + dt.timedelta(days=b - 1)
        late += int((df["date"] != day).sum())
    assert 0.015 < late / delivered < 0.05
