import dataclasses

import numpy as np
import pytest

from dq import spec
from dq.faults import Condition, conditions, inject, verify
from dq.world import generate_stream


@pytest.fixture(scope="module")
def clean():
    return generate_stream(3601, "W1")


@pytest.fixture(scope="module")
def all_conditions():
    return conditions([3601], ["W1"])


def _cond(fault, severity, onset=30, column=None, world="W1", seed=3601):
    return Condition(world, seed, fault, severity, onset, column, f"test-{fault}-{severity}")


def _copy(stream):
    return dataclasses.replace(stream, batches=[df.copy() for df in stream.batches])


def test_conditions_cover_the_grid():
    conds = conditions(spec.SEEDS, ["W1"])
    assert len(conds) == 120
    both = conditions(spec.SEEDS, ["W1", "W0"])
    assert len(both) == 240
    assert len({c.id for c in both}) == 240


def test_onsets_are_in_range_and_shared_by_worlds():
    both = conditions(spec.SEEDS, ["W1", "W0"])
    lo, hi = spec.ONSET_RANGE
    assert all(lo <= c.onset <= hi for c in both)
    by_key = {}
    for c in both:
        by_key.setdefault((c.seed, c.fault, c.severity), set()).add((c.onset, c.column))
    assert all(len(v) == 1 for v in by_key.values())
    assert len({c.onset for c in both}) > 10  # not all the same


def test_only_null_spike_has_a_column():
    for c in conditions([3601, 3602], ["W1"]):
        if c.fault == "null_spike":
            assert c.column in ("temp_max_c", "precip_mm")
        else:
            assert c.column is None


def test_conditions_are_deterministic_and_hashable():
    first = conditions([3603], ["W1"])
    assert first == conditions([3603], ["W1"])
    assert first[0] in set(first)


def test_every_fault_is_exactly_what_it_says(clean, all_conditions):
    assert len(all_conditions) == 24
    for cond in all_conditions:
        faulted = inject(clean, cond)
        assert verify(clean, faulted, cond) == [], cond.id


def test_every_fault_in_the_other_world_and_seed():
    other = generate_stream(3604, "W0")
    for cond in conditions([3604], ["W0"]):
        assert verify(other, inject(other, cond), cond) == [], cond.id


@pytest.mark.parametrize("fault", ["freshness_lapse", "volume", "schema_drift"])
def test_faults_that_reach_the_end_of_the_stream(clean, fault):
    # onset 54: the window ends at batch 60, so a lapse's backlog would land past the stream
    for severity in spec.SEVERITIES:
        cond = _cond(fault, severity, onset=54)
        assert verify(clean, inject(clean, cond), cond) == [], cond.id


def test_inject_never_mutates_its_input(clean, all_conditions):
    before = [df.copy(deep=True) for df in clean.batches]
    cities = clean.cities.copy(deep=True)
    for cond in all_conditions:
        faulted = inject(clean, cond)
        assert faulted is not clean
        assert all(a is not b for a, b in zip(faulted.batches, clean.batches, strict=True))
    assert all(a.equals(b) for a, b in zip(before, clean.batches, strict=True))
    assert clean.cities.equals(cities)


def test_inject_is_deterministic_and_leaves_cities_alone(clean, all_conditions):
    for cond in all_conditions[::5]:
        one, two = inject(clean, cond), inject(clean, cond)
        assert all(a.equals(b) for a, b in zip(one.batches, two.batches, strict=True))
        assert one.cities.equals(clean.cities)


def test_batches_outside_the_window_are_untouched(clean):
    cond = _cond("null_spike", "large", onset=30, column="precip_mm")
    faulted = inject(clean, cond)
    for b in range(1, 61):
        same = clean.batches[b - 1].equals(faulted.batches[b - 1])
        assert same == (b not in cond.window)


def test_freshness_backlog_lands_after_the_lapse(clean):
    cond = _cond("freshness_lapse", "medium", onset=30)
    faulted = inject(clean, cond)
    assert [len(faulted.batches[b - 1]) for b in (30, 31, 32)] == [0, 0, 0]
    landed = faulted.batches[32]  # batch 33
    want = sum(len(clean.batches[b - 1]) for b in (30, 31, 32, 33))
    assert len(landed) == want
    assert (landed["ingested_at"] == clean.ingest_times[32]).all()
    assert str(faulted.batches[29]["ingested_at"].dtype) == "datetime64[us, UTC]"


def test_freshness_backlog_is_dropped_past_the_stream_end(clean):
    cond = _cond("freshness_lapse", "large", onset=54)
    faulted = inject(clean, cond)
    assert all(len(faulted.batches[b - 1]) == 0 for b in range(54, 61))


def test_replay_doubles_the_batch_without_compounding(clean):
    cond = _cond("volume", "large", onset=30)
    faulted = inject(clean, cond)
    for b in cond.window:
        assert len(faulted.batches[b - 1]) == len(clean.batches[b - 1]) + len(clean.batches[b - 2])


def test_volume_drop_keeps_a_fixed_subset(clean):
    cond = _cond("volume", "medium", onset=30)
    faulted = inject(clean, cond)
    seen = [set(faulted.batches[b - 1]["city_id"]) for b in cond.window]
    union = set().union(*seen)
    assert 85 <= len(union) <= 100  # half of the ~190 stations registered by batch 36
    assert all(s <= union for s in seen)


def test_orphan_ids_are_unknown_to_cities(clean):
    cond = _cond("orphan_fk", "medium", onset=30)
    faulted = inject(clean, cond)
    ids = set(faulted.batches[30]["city_id"]) - set(clean.cities["city_id"])
    assert ids and all(i.startswith("unregistered-") for i in ids)


def test_silent_shift_moves_every_temperature(clean):
    cond = _cond("silent_shift", "large", onset=30)
    faulted = inject(clean, cond)
    diff = (faulted.batches[30]["temp_max_c"] - clean.batches[30]["temp_max_c"]).dropna()
    assert np.allclose(diff, 5.0)
    assert faulted.batches[30]["temp_max_c"].max() < 60


def test_schema_drift_variants(clean):
    small = inject(clean, _cond("schema_drift", "small")).batches[30]
    medium = inject(clean, _cond("schema_drift", "medium")).batches[30]
    large = inject(clean, _cond("schema_drift", "large")).batches[30]
    assert str(small["temp_max_c"].dtype) == "float32"
    assert "tmax_c" in medium.columns and "temp_max_c" not in medium.columns
    original = list(clean.batches[30].columns)
    assert list(medium.columns).index("tmax_c") == original.index("temp_max_c")
    assert "temp_max_c" not in large.columns


def _null_spike_case(clean):
    cond = _cond("null_spike", "small", onset=30, column="temp_max_c")
    return cond, inject(clean, cond)


def _extra_null(good):
    s = _copy(good)
    df = s.batches[31]
    i = int(np.flatnonzero(df["temp_max_c"].notna().to_numpy())[0])
    df.iloc[i, df.columns.get_loc("temp_max_c")] = np.nan
    return s


def _touched_outside_window(good):
    s = _copy(good)
    s.batches[10].iloc[0, 2] = 99.0
    return s


def _dropped_row(good):
    s = _copy(good)
    s.batches[31] = s.batches[31].iloc[1:].reset_index(drop=True)
    return s


def _wrong_dtype(good):
    s = _copy(good)
    s.batches[31]["temp_min_c"] = s.batches[31]["temp_min_c"].astype("float32")
    return s


def _cities_changed(good):
    return dataclasses.replace(good, cities=good.cities.iloc[:-1])


@pytest.mark.parametrize(
    "mutate",
    [_extra_null, _touched_outside_window, _dropped_row, _wrong_dtype, _cities_changed],
    ids=lambda f: f.__name__.strip("_"),
)
def test_verify_catches_a_wrong_injection(clean, mutate):
    cond, good = _null_spike_case(clean)
    assert verify(clean, good, cond) == []
    assert verify(clean, mutate(good), cond) != []


def test_verify_catches_the_wrong_fault_or_none(clean):
    cond = _cond("duplicates", "medium", onset=30)
    wrong = inject(clean, _cond("orphan_fk", "medium", onset=30))
    assert verify(clean, wrong, cond) != []
    assert verify(clean, clean, cond) != []


def test_verify_catches_a_shifted_onset(clean):
    cond = _cond("silent_shift", "small", onset=30)
    late = inject(clean, _cond("silent_shift", "small", onset=31))
    assert verify(clean, late, cond) != []


def test_orphan_ids_never_repeat_a_key_across_batches(clean):
    for severity in spec.SEVERITIES:
        cond = _cond("orphan_fk", severity)
        faulted = inject(clean, cond)
        ids = [
            v for df in faulted.batches for v in df["city_id"] if str(v).startswith("unregistered-")
        ]
        assert len(ids) == len(set(ids))
        assert verify(clean, faulted, cond) == []


def test_orphan_oracle_catches_a_reused_key(clean):
    cond = _cond("orphan_fk", "small")
    faulted = _copy(inject(clean, cond))
    first, second = faulted.batches[cond.onset - 1], faulted.batches[cond.onset]
    orphan = first.index[first["city_id"].astype(str).str.startswith("unregistered-")][0]
    target = second.index[second["city_id"].astype(str).str.startswith("unregistered-")][0]
    # The same orphan key delivered again one batch later: a duplicate the label does not name.
    second.loc[target, "city_id"] = first.loc[orphan, "city_id"]
    second.loc[target, "date"] = first.loc[orphan, "date"]
    assert any("repeat elsewhere" in p for p in verify(clean, faulted, cond))
