"""Gate G5 (lake versions) and unit tests for the lake source."""

from __future__ import annotations

import datetime as dt

import pyarrow as pa
import pytest

pytest.importorskip("lakehouse")

from lakehouse import contract, tables
from lakehouse.spec import ARROW_SCHEMA_V1

from dq import lake, spec
from dq.summary import SUMMARY_COLUMNS

T0 = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


def at(days: float = 0.0, seconds: float = 0.0) -> dt.datetime:
    return T0 + dt.timedelta(days=days, seconds=seconds)


def never(a: int, b: int) -> bool:  # same_rows callback for cases that must not need it
    raise AssertionError("same_rows should not be consulted")


def kinds(versions) -> list[str]:
    return [v.kind for v in versions]


def alarms(results, check=None, version=None):
    return [
        r
        for r in results
        if r.alarm
        and (check is None or r.check.startswith(check))
        and (version is None or r.batch == version)
    ]


# --- pure: logical_versions --------------------------------------------------------------


def appends(n: int, each: int = 200) -> list[lake.Snap]:
    return [(i + 1, at(i), each * (i + 1)) for i in range(n)]


def test_compaction_pair_is_one_maintenance_version():
    snaps = [*appends(3), (10, at(5), 0), (11, at(5, 0.1), 600)]
    vs = lake.logical_versions(snaps, lambda a, b: True)
    assert kinds(vs) == ["append", "append", "append", "maintenance"]
    m = vs[-1]
    assert m.snapshot_id == 11 and m.folded == (10,)
    # a compaction brings no data, so freshness keeps the last real append's time
    assert m.data_committed_at == at(2)
    assert m.committed_at == at(5, 0.1)


def test_truncate_pair_is_an_overwrite_to_zero():
    snaps = [*appends(3), (10, at(5), 0), (11, at(5, 0.1), 0)]
    vs = lake.logical_versions(snaps, lambda a, b: False)
    assert kinds(vs) == ["append", "append", "append", "overwrite"]
    assert vs[-1].total_records == 0 and vs[-1].folded == (10,)


def test_unpaired_zero_snapshot_is_empty():
    snaps = [*appends(2), (10, at(5), 0), (11, at(5, 61), 400)]  # 61 s apart: not a pair
    vs = lake.logical_versions(snaps, never)
    assert kinds(vs) == ["append", "append", "empty", "append"]
    assert vs[2].folded == ()


def test_pair_with_different_rows_is_an_overwrite():
    snaps = [*appends(2), (10, at(5), 0), (11, at(5, 0.1), 400)]  # same count, other rows
    vs = lake.logical_versions(snaps, lambda a, b: False)
    assert kinds(vs) == ["append", "append", "overwrite"]
    # different count: rows cannot be equal, the callback is not needed
    snaps = [*appends(2), (10, at(5), 0), (11, at(5, 0.1), 300)]
    assert kinds(lake.logical_versions(snaps, never)) == ["append", "append", "overwrite"]


def test_leading_empty_snapshot():
    snaps = [(1, at(0), 0), (2, at(1), 200), (3, at(2), 400)]
    vs = lake.logical_versions(snaps, never)
    assert kinds(vs) == ["empty", "append", "append"]


def test_unfolded_snapshot_with_fewer_or_equal_records_is_an_overwrite():
    snaps = [(1, at(0), 500), (2, at(1), 400), (3, at(2), 400), (4, at(3), 450)]
    assert kinds(lake.logical_versions(snaps, never)) == [
        "append",
        "overwrite",
        "overwrite",
        "append",
    ]


def test_naive_versions_treat_every_snapshot_as_data():
    snaps = [*appends(2), (10, at(5), 0), (11, at(5, 0.1), 400)]
    vs = lake.naive_versions(snaps)
    assert kinds(vs) == ["append", "append", "empty", "append"]
    assert all(v.data_committed_at == v.committed_at for v in vs)


# --- synthetic snapshots with no Iceberg -------------------------------------------------


def events(ids, *, amount=1.0, name="page_view", drop=()) -> pa.Table:
    n = len(ids)
    cols = {
        "event_id": pa.array(list(ids), pa.int64()),
        "user_id": pa.array([1] * n, pa.int64()),
        "event_ts": pa.array([T0] * n, pa.timestamp("us", tz="UTC")),
        "event_name": pa.array([name] * n, pa.string()),
        "channel": pa.array(["organic"] * n, pa.string()),
        "platform": pa.array(["web"] * n, pa.string()),
        "amount": pa.array([amount] * n, pa.float64()),
    }
    return pa.table({c: v for c, v in cols.items() if c not in drop})


def synthetic(compaction_day: float = 13):
    """Ten daily appends of 200 rows (days 0-9), then a compaction pair."""
    snaps = [(i + 1, at(i), 200 * (i + 1)) for i in range(10)]
    snaps += [(100, at(compaction_day), 0), (101, at(compaction_day, 0.1), 2000)]
    contents = {s[0]: events(range(s[2])) for s in snaps}
    contents[100] = events([])
    return snaps, contents


def run(snaps, contents, walk, check_time, **kw):
    return lake.run_lake_checks(
        None, walk=walk, check_time=check_time, snaps=snaps, read=contents.__getitem__, **kw
    )


def test_compaction_does_not_hide_staleness_in_the_logical_walk():
    snaps, contents = synthetic(compaction_day=13)  # last append day 9, compaction day 13
    check_time = at(14)
    logical = run(snaps, contents, "logical", check_time)
    naive = run(snaps, contents, "naive", check_time)

    fresh_l = next(r for r in logical if r.check == "freshness.last_data_commit")
    fresh_n = next(r for r in naive if r.check == "freshness.last_data_commit")
    assert fresh_l.status == "fail" and fresh_l.observed >= 72
    assert fresh_n.status == "pass" and fresh_n.observed == pytest.approx(24.0, abs=0.01)
    assert fresh_l.snapshot_id == 101


def test_synthetic_walks_differ_on_volume_only_for_the_naive_one():
    snaps, contents = synthetic()
    logical = run(snaps, contents, "logical", at(14))
    naive = run(snaps, contents, "naive", at(14))
    assert not alarms(logical, "volume")
    assert len(alarms(naive, "volume")) >= 1
    # maintenance: table-level checks only
    m = [r for r in logical if r.batch == 10]
    assert {r.check for r in m} == {
        "schema.contract_lake",
        "unique.event_id",
        "freshness.last_data_commit",
    }
    assert all(r.table == "lake.events" and r.snapshot_id is not None for r in logical)


def test_schema_check_classifies_changes():
    ok = events(range(5))
    extra = ok.append_column("country", pa.array(["IN"] * 5))
    renamed = ok.rename_columns(["event_id", "user_id", "ts", *ok.column_names[3:]])
    retyped = ok.set_column(0, "event_id", pa.array(["a"] * 5, pa.string()))  # int64 -> string
    large = ok.cast(
        pa.schema(
            [pa.field(f.name, pa.large_string() if f.type == pa.string() else f.type)
             for f in ok.schema]
        )
    )  # fmt: skip
    got = {}
    for name, t in dict(ok=ok, extra=extra, renamed=renamed, retyped=retyped, large=large).items():
        snaps = [(1, at(0), 5)]
        res = lake.run_lake_checks(None, snaps=snaps, read=lambda _s, t=t: t, check_time=at(0.1))
        got[name] = next(r.status for r in res if r.check == "schema.contract_lake")
    assert got == {
        "ok": "pass",
        "extra": "warn",
        "renamed": "fail",
        "retyped": "fail",
        "large": "pass",
    }


def test_lake_summary_has_the_declared_layout():
    snaps, contents = synthetic()
    res = run(snaps, contents, "logical", at(14))
    versions = lake.lake_versions(None, "logical", snaps=snaps, read=contents.__getitem__)
    df = lake.lake_summary(res, versions, "logical")
    assert list(df.columns) == list(SUMMARY_COLUMNS["lake"])
    assert len(df) == len(res)
    assert set(df["walk"]) == {"logical"}
    assert df.loc[df["version"] == 10, "kind"].eq("maintenance").all()


# --- a real #35 table --------------------------------------------------------------------

ROWS = 2000


@pytest.fixture(scope="module")
def compacted(tmp_path_factory):
    table = contract.build_canonical_table(tmp_path_factory.mktemp("canonical"), rows=ROWS)
    before = contract.snapshots(table)[-1][0]
    tables.compact(table, "L1")
    return table, before


def test_logical_walk_is_quiet_after_compaction_and_naive_walk_is_not(compacted):
    table, _ = compacted
    logical = lake.run_lake_checks(table, walk="logical")
    naive = lake.run_lake_checks(table, walk="naive")

    assert not alarms(logical, "volume")
    assert not alarms(logical, "freshness")
    assert not alarms(logical)  # nothing else fires either
    assert len(alarms(naive, "volume")) >= 1


def test_compaction_folds_into_one_maintenance_version(compacted):
    table, _ = compacted
    snaps = contract.snapshots(table)
    assert len(snaps) == 12  # 10 appends + DELETE + APPEND
    vs = lake.logical_versions(snaps, lake.same_rows_from_table(table))
    assert kinds(vs) == ["append"] * 10 + ["maintenance"]
    m = vs[-1]
    assert m.folded == (snaps[10][0],) and m.snapshot_id == snaps[11][0]
    assert m.data_committed_at == vs[9].committed_at
    # the folded half is a DELETE in PyIceberg itself, the other half an APPEND
    assert str(table.snapshot_by_id(snaps[10][0]).summary.operation.value) == "delete"
    assert str(table.snapshot_by_id(snaps[11][0]).summary.operation.value) == "append"
    assert snaps[10][2] == 0 and snaps[11][2] == ROWS
    gap = (snaps[11][1] - snaps[10][1]).total_seconds()
    print(f"DELETE -> APPEND gap: {gap * 1000:.0f} ms")
    assert 0 <= gap <= spec.PAIR_GAP_SECONDS


def test_same_rows_and_time_travel_agree_across_compaction(compacted):
    table, before = compacted
    after = contract.snapshots(table)[-1][0]
    same_rows = lake.same_rows_from_table(table)
    assert same_rows(before, after)
    assert not same_rows(contract.snapshots(table)[0][0], after)

    old = contract.to_arrow(table, before).sort_by("event_id")
    new = contract.to_arrow(table).sort_by("event_id")
    assert old.num_rows == ROWS
    assert old.equals(new)


def test_truncate_is_flagged_by_both_walks(tmp_path):
    table = contract.build_canonical_table(tmp_path, rows=ROWS)
    table.overwrite(ARROW_SCHEMA_V1.empty_table().cast(table.schema().as_arrow()))
    snaps = contract.snapshots(table)
    assert [s[2] for s in snaps[-2:]] == [0, 0]

    logical = lake.run_lake_checks(table, walk="logical")
    naive = lake.run_lake_checks(table, walk="naive")
    vs = lake.logical_versions(snaps, lake.same_rows_from_table(table))
    assert vs[-1].kind == "overwrite" and vs[-1].total_records == 0
    assert alarms(logical, "volume", version=vs[-1].index)
    assert alarms(naive, "volume")


# --- the demo table ----------------------------------------------------------------------


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    table = lake.build_demo(tmp_path_factory.mktemp("demo"), rows=ROWS)
    return table, lake.run_lake_checks(table, walk="logical")


def test_demo_versions(demo):
    table, _ = demo
    vs = lake.lake_versions(table, "logical")
    assert kinds(vs) == ["append"] * 10 + ["maintenance"] + ["append"] * 6
    assert [v.total_records for v in vs[11:]] == [2200, 2400, 2600, 2800, 3000, 3100]


def test_demo_clean_appends_raise_nothing(demo):
    _, res = demo
    for version in (11, 12):
        assert not alarms(res, version=version), alarms(res, version=version)
    assert not alarms(res, version=10)  # the compaction


def test_demo_each_fault_is_flagged_by_its_family(demo):
    _, res = demo
    # (a) null amounts on purchases
    assert alarms(res, "null_rate", version=13)
    # (b) repeated event_ids; quiet before it
    assert alarms(res, "unique", version=14)
    assert not alarms(res, "unique", version=13)
    # (c) negative amounts
    assert alarms(res, "accepted_range", version=15)
    # (d) half-size append
    assert alarms(res, "volume", version=16)
    # and the families that should stay quiet do
    assert not alarms(res, "accepted_range", version=13)
    assert not alarms(res, "accepted_range", version=14)
    assert not alarms(res, "null_rate", version=15)
    assert not alarms(res, "volume", version=13)
    assert not alarms(res, "volume", version=15)
    assert not alarms(res, "schema")
    assert not alarms(res, "freshness")
