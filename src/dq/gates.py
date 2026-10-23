"""Correctness gates G1, G4 and G5, run as one command and recorded with the commit.

G2 and G3 run inside `dq simulate` and G6 inside `dq evaluate`; `dq evaluate` refuses to print
verdicts unless all six passed on the same clean commit.
"""

from __future__ import annotations

import datetime as dt
import shutil
from collections.abc import Callable
from pathlib import Path

from dq import spec

Gate = tuple[bool, str]  # passed, detail


def g1_contract(workdir: Path) -> Gate:
    """The fixture's tables, columns and types equal the contract; keys hold; marts build."""
    from dq.contract import compare_columns, load_contract
    from dq.fixture import build_fixture
    from dq.warehouse import DuckDBWarehouse

    contract = load_contract()
    db = build_fixture(workdir / "g1.duckdb")
    problems: list[str] = []
    with DuckDBWarehouse(db) as wh:
        for name, table in contract.tables.items():
            problems += [f"{name}: {d}" for d in compare_columns(table.columns, wh.columns(name))]
            rows = int(wh.fetch_df(f"select count(*) as n from {name}")["n"].iloc[0])
            if rows == 0:
                problems.append(f"{name}: empty")
            if table.rows is not None and rows != table.rows:
                problems.append(f"{name}: {rows} rows, contract says {table.rows}")
        queries = {
            "duplicate weather keys": "select count(*) - count(distinct (city_id, date)) "
            "as n from raw.weather_daily",
            "orphan city_id": "select count(*) as n from raw.weather_daily w "
            "left join raw.cities c using (city_id) where c.city_id is null",
            "min above max": "select count(*) as n from raw.weather_daily "
            "where temp_min_c > temp_max_c",
        }
        for label, sql in queries.items():
            n = int(wh.fetch_df(sql)["n"].iloc[0])
            if n:
                problems.append(f"{label}: {n}")
    return not problems, "; ".join(problems) or f"{len(contract.tables)} tables match"


def g4_parity(seed: int = spec.SMOKE_SEEDS[0]) -> Gate:
    """Great Expectations and own code agree on every static verdict of the smoke grid."""
    from dq import faults, gx_parity, world
    from dq.checkdefs import load_checks
    from dq.checks import run_static
    from dq.contract import load_contract
    from dq.simulate import contexts

    suite, contract = load_checks(), load_contract()
    columns = contract.tables[suite.table].columns
    static = [c for c in suite.checks if c.kind == "static"]
    clean = world.generate_stream(seed, "W1")
    streams = [("clean", clean)] + [
        (c.id, faults.inject(clean, c)) for c in faults.conditions((seed,), ("W1",))
    ]
    mismatches: list[str] = []
    compared = 0
    for sid, stream in streams:
        for ctx in contexts(stream):
            gx = gx_parity.gx_static_results(suite, ctx, columns)
            own = [run_static(c, ctx, contract) for c in static]
            compared += len(gx)
            mismatches += [f"{sid}: {m}" for m in gx_parity.parity(own, gx)]
    detail = f"{compared} verdicts over {len(streams)} streams, {len(mismatches)} mismatches"
    if mismatches:
        detail += f"; first: {mismatches[0]}"
    return not mismatches, detail


def g5_lake(workdir: Path, rows: int = 2_000) -> Gate:
    """On a real #35 table: compaction is silent in the logical walk and loud in the naive one,
    a truncate is flagged by both, and time travel across the compaction is intact."""
    from lakehouse import contract, tables

    from dq import lake

    problems: list[str] = []
    table = contract.build_canonical_table(workdir / "g5", rows=rows)
    before = contract.snapshots(table)[-1][0]
    tables.compact(table, "L1")
    table = contract.open_table(warehouse=workdir / "g5")
    now = dt.datetime.now(dt.UTC)

    def volume_alarms(walk: str) -> int:
        results = lake.run_lake_checks(table, walk=walk, check_time=now)
        return sum(r.alarm for r in results if r.family == "volume")

    logical = lake.run_lake_checks(table, walk="logical", check_time=now)
    if any(r.alarm for r in logical):
        problems.append("logical walk alarms on a compacted clean table")
    if volume_alarms("naive") < 1:
        problems.append("naive walk raised no volume alarm at the compaction")
    if not lake.same_rows_from_table(table)(before, contract.snapshots(table)[-1][0]):
        problems.append("pre- and post-compaction rows differ")

    empty = contract.to_arrow(table).slice(0, 0)
    table.overwrite(empty)
    table = contract.open_table(warehouse=workdir / "g5")
    for walk in ("logical", "naive"):
        if volume_alarms(walk) < 1:
            problems.append(f"{walk} walk missed the truncate")
    if contract.to_arrow(table, before).num_rows != rows:
        problems.append("time travel to the pre-compaction snapshot lost rows")
    return not problems, "; ".join(problems) or "compaction silent, truncate flagged, travel intact"


GATES: dict[str, Callable[[Path], Gate]] = {
    "G1": g1_contract,
    "G4": lambda _workdir: g4_parity(),
    "G5": g5_lake,
}


def run_gates(workdir: Path, only: tuple[str, ...] = tuple(GATES)) -> dict[str, dict[str, object]]:
    """Run the gates with scratch files under `workdir`, which is removed afterwards."""
    out: dict[str, dict[str, object]] = {}
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        for name in only:
            try:
                passed, detail = GATES[name](workdir)
            except Exception as exc:  # a crashing gate is a failed gate, with the reason
                passed, detail = False, f"{type(exc).__name__}: {exc}"
            out[name] = {"passed": passed, "detail": detail}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return out
