"""Command-line entry point: `dq <command>`."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from dq import spec

RAW_DIR = Path("results/raw")
RESULTS_DIR = Path("results")
STORE = Path("results/dq.sqlite")


def _git_sha() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    dirty = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
    return out.stdout.strip() + ("-dirty" if dirty.stdout.strip() else "")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- contract / fixture / run -----------------------------------------------------------


def cmd_contract(args: argparse.Namespace) -> int:
    from dq.contract import compare_columns, load_contract

    contract = load_contract()
    if not (args.fixture or args.postgres):
        for name, table in contract.tables.items():
            print(f"{name}: {len(table.columns)} columns")
        return 0
    wh = _warehouse(args)
    problems = []
    with wh:
        for name, table in contract.tables.items():
            for diff in compare_columns(table.columns, wh.columns(name)):
                problems.append(f"{name}: {diff}")
    for p in problems:
        print(p)
    print("contract OK" if not problems else f"{len(problems)} differences")
    return 1 if problems else 0


def _warehouse(args: argparse.Namespace):
    from dq.warehouse import DuckDBWarehouse, PostgresWarehouse

    if getattr(args, "postgres", False):
        dsn = os.environ.get("DQ_POSTGRES_DSN")
        if not dsn:
            raise SystemExit("DQ_POSTGRES_DSN is not set (see .env.example)")
        return PostgresWarehouse(dsn)
    return DuckDBWarehouse(args.fixture)


def cmd_build_fixture(args: argparse.Namespace) -> int:
    from dq.fixture import build_fixture

    path = build_fixture(args.out, seed=args.seed)
    print(f"fixture written: {path}")
    return 0


def _catalog_contract_result(defn, contract, catalog: dict[str, str]):
    from dq.contract import compare_columns
    from dq.types import CheckResult

    diffs = compare_columns(contract.tables[defn_table := "raw.weather_daily"].columns, catalog)
    return CheckResult(
        check=defn.id,
        family=defn.family,
        table=defn_table,
        batch=None,
        status="fail" if diffs else "pass",
        observed=float(len(diffs)),
        expected="catalogue columns and types equal the contract",
        detail="; ".join(diffs),
    )


def cmd_run(args: argparse.Namespace) -> int:
    """Every static check of arm A on a warehouse's raw.weather_daily, as one batch."""
    import pandas as pd

    from dq import sinks
    from dq.checkdefs import load_checks
    from dq.checks import run_static
    from dq.contract import load_contract
    from dq.types import BatchContext

    suite, contract = load_checks(), load_contract()
    with _warehouse(args) as wh:
        df = wh.fetch_df("select * from raw.weather_daily")
        parents = wh.fetch_df("select city_id from raw.cities")
        catalog = wh.columns(suite.table)
    # Drivers hand DATE back as timestamps; the checks read the batch-frame convention
    # (datetime.date), and the contract check reads the warehouse's own catalogue instead.
    if "date" in df.columns and pd.api.types.is_datetime64_any_dtype(df["date"]):
        df["date"] = df["date"].dt.date
    check_time = (
        dt.datetime.fromisoformat(args.check_time) if args.check_time else dt.datetime.now(dt.UTC)
    )
    if check_time.tzinfo is None:
        check_time = check_time.replace(tzinfo=dt.UTC)
    latest = df["ingested_at"].max() if len(df) else None
    ctx = BatchContext(
        table=suite.table,
        batch=None,
        batch_df=df,
        loaded_keys=frozenset(),
        parent_keys=frozenset(parents["city_id"].astype(str)),
        max_ingested_at=None if latest is None else latest.to_pydatetime(),
        check_time=check_time,
    )
    results = []
    for c in suite.for_arm("A"):
        if c.kind != "static":
            continue
        if c.rule == "contract_equal":
            results.append(_catalog_contract_result(c, contract, catalog))
        else:
            results.append(run_static(c, ctx, contract))
    sinks.to_stdout(results)
    if args.json:
        sinks.write_json(results, args.json)
    if args.slack:
        status = sinks.send_slack(results, "dq run", sinks.UrllibTransport())
        print("slack: not sent (no alarms or no webhook)" if status is None else f"slack: {status}")
    if args.store:
        from dq import store

        store.init(args.store)
        run_id = store.write_run(
            args.store, "dq run", "run", {"source": "postgres" if args.postgres else "duckdb"},
            git_sha=_git_sha(),
        )  # fmt: skip
        store.write_results(
            args.store, run_id, store.results_from_check_results(results, "warehouse"),
            source="warehouse",
        )  # fmt: skip
    return 1 if any(r.alarm for r in results) else 0


# --- the experiment ---------------------------------------------------------------------


def _scale(name: str) -> tuple[tuple[int, ...], tuple[str, ...]]:
    if name == "smoke":
        return spec.SMOKE_SEEDS, ("W1",)
    return spec.SEEDS, spec.WORLDS


def cmd_simulate(args: argparse.Namespace) -> int:
    from dq.simulate import run_grid

    seeds, worlds = _scale(args.scale)
    ks = spec.K_GRID
    started = dt.datetime.now(dt.UTC)
    grid = run_grid(seeds, worlds, ks, jobs=args.jobs)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    grid.results.to_parquet(out / "results.parquet", index=False)
    grid.conditions.to_csv(out / "conditions.csv", index=False)
    problems = {k: v for k, v in grid.verify_problems.items() if v}
    manifest = {
        "scale": args.scale,
        "seeds": list(seeds),
        "worlds": list(worlds),
        "ks": list(ks),
        "git_sha": _git_sha(),
        "started": started.isoformat(timespec="seconds"),
        "seconds": round((dt.datetime.now(dt.UTC) - started).total_seconds(), 1),
        "rows": len(grid.results),
        "results_sha256": _sha256(out / "results.parquet"),
        "g2_problems": problems,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"{len(grid.results):,} results in {manifest['seconds']} s -> {out}")
    print(f"results sha256 {manifest['results_sha256']}")
    if problems:
        print(f"G2 FAILED: {len(problems)} conditions do not match their label")
        for cid, ps in problems.items():
            print(f"  {cid}: {ps[0]}")
        return 2
    print("G2 passed: every injected fault matches its label")
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    import pandas as pd

    from dq import evaluate, report
    from dq.checkdefs import load_checks

    raw = Path(args.raw)
    manifest = json.loads((raw / "manifest.json").read_text(encoding="utf-8"))
    results = pd.read_parquet(raw / "results.parquet")
    conds = pd.read_csv(raw / "conditions.csv")
    if manifest["g2_problems"]:
        print("run invalid: G2 failed in simulate; no verdicts")
        return 2
    gates = {
        "G6 attribution (clean vs itself)": evaluate.gate_g6(results),
        "attribution before onset": evaluate.gate_attribution_before_onset(results),
        "S0 silent on clean streams": evaluate.gate_s0_silent_on_clean(results, load_checks()),
    }
    failed = {name: ps for name, ps in gates.items() if ps}
    for name, ps in gates.items():
        print(f"{'FAIL' if ps else 'pass'}  {name}" + (f": {ps[0]}" if ps else ""))
    if failed:
        print("run invalid: a gate failed; no verdicts")
        return 2

    tables = evaluate.evaluate_tables(results, conds, ks=manifest["ks"])
    meta = {
        "scale": manifest["scale"],
        "seeds": manifest["seeds"],
        "worlds": manifest["worlds"],
        "ks": manifest["ks"],
        "git_sha": manifest["git_sha"],
        "results_sha256": manifest["results_sha256"],
        "k_default": spec.K_DEFAULT,
    }
    path = report.write_results(tables, meta, args.out)
    print(f"report: {path}")
    for _, row in tables["verdicts"].iterrows():
        print(f"{row['hypothesis']} {row['world']}: {row['verdict']}: {row['detail']}")
    if args.store:
        from dq import store

        store.init(args.store)
        run_id = store.write_run(
            args.store, args.label or f"{manifest['scale']} grid", manifest["scale"], meta,
            git_sha=manifest["git_sha"],
        )  # fmt: skip
        for name, df in tables.items():
            store.write_summary(args.store, run_id, name, df)
        clean = results.loc[
            (results["stream"] == "clean")
            & (results["world"] == manifest["worlds"][0])
            & (results["seed"] == manifest["seeds"][0])
            & ((results["mode"] != "frozen") & (results["k"].isna() | (results["k"] == 4.0)))
        ]
        clean = clean.rename(columns={"check": "check_id"}).drop(
            columns=["fault", "severity", "onset", "column"]
        )
        store.write_results(args.store, run_id, clean, source="stream")
        print(f"store: {args.store} (run {run_id})")
    return 0


# --- lake, parity, dashboard ------------------------------------------------------------


def cmd_lake(args: argparse.Namespace) -> int:
    import pandas as pd

    from dq import lake

    try:
        from lakehouse import contract
    except ImportError as err:
        msg = "the lake source needs the `lake` dependency group: uv sync --group lake"
        raise SystemExit(msg) from err

    if args.demo:
        table = lake.build_demo(args.warehouse, rows=args.rows)
    else:
        table = contract.open_table(warehouse=args.warehouse)
    check_time = dt.datetime.now(dt.UTC)
    frames = []
    for walk in ("logical", "naive"):
        results = lake.run_lake_checks(table, walk=walk, check_time=check_time)
        versions = lake.lake_versions(table, walk=walk)
        frames.append(lake.lake_summary(results, versions, walk))
        alarms = [r for r in results if r.alarm]
        print(f"{walk}: {len(versions)} versions, {len(alarms)} alarms")
        for r in alarms:
            print(f"  v{r.batch} snapshot {r.snapshot_id}  {r.check}  {r.status}  {r.detail}")
    summary = pd.concat(frames, ignore_index=True)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        summary.to_csv(args.out, index=False)
        print(f"lake summary: {args.out}")
    if args.store:
        from dq import store

        store.init(args.store)
        run_id = store.latest_run_id(args.store)
        if run_id is None:
            run_id = store.write_run(args.store, "lake", "lake", {}, git_sha=_git_sha())
        store.write_summary(args.store, run_id, "lake", summary)
        print(f"store: {args.store} (run {run_id})")
    return 0


def cmd_gx_parity(args: argparse.Namespace) -> int:
    from dq import faults, gx_parity, world
    from dq.checkdefs import load_checks
    from dq.checks import run_static
    from dq.contract import load_contract
    from dq.simulate import contexts

    suite, contract = load_checks(), load_contract()
    columns = contract.tables[suite.table].columns
    static = [c for c in suite.checks if c.kind == "static"]
    clean = world.generate_stream(args.seed, "W1")
    streams = [("clean", clean)] + [
        (c.id, faults.inject(clean, c)) for c in faults.conditions((args.seed,), ("W1",))
    ]
    mismatches: list[str] = []
    compared = 0
    for sid, stream in streams:
        for ctx in contexts(stream):
            gx = gx_parity.gx_static_results(suite, ctx, columns)
            own = [run_static(c, ctx, contract) for c in static]
            compared += len(gx)
            mismatches += [f"{sid}: {m}" for m in gx_parity.parity(own, gx)]
    print(f"G4: {compared:,} (check, batch) verdicts compared over {len(streams)} streams")
    for m in mismatches[:50]:
        print(f"  {m}")
    print("G4 passed" if not mismatches else f"G4 FAILED: {len(mismatches)} mismatches")
    return 1 if mismatches else 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    from dq import dashboard

    path = dashboard.build(args.store, args.out)
    print(f"dashboard: {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dq", description="Data quality checks, measured.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("contract", help="print #22's contract, or compare a warehouse with it")
    p.add_argument("--fixture", help="DuckDB file to compare")
    p.add_argument("--postgres", action="store_true", help="compare $DQ_POSTGRES_DSN instead")
    p.set_defaults(fn=cmd_contract)

    p = sub.add_parser("build-fixture", help="build the DuckDB warehouse shaped like #22")
    p.add_argument("--out", required=True)
    p.add_argument("--seed", type=int, default=spec.FIXTURE_SEED)
    p.set_defaults(fn=cmd_build_fixture)

    p = sub.add_parser("run", help="run the static checks on a warehouse's raw.weather_daily")
    p.add_argument("--fixture", help="DuckDB file")
    p.add_argument("--postgres", action="store_true", help="use $DQ_POSTGRES_DSN")
    p.add_argument("--check-time", help="ISO time the checks run at (default: now)")
    p.add_argument("--json", help="write every result to this JSON file")
    p.add_argument("--slack", action="store_true", help="post alarms to $DQ_SLACK_WEBHOOK_URL")
    p.add_argument("--store", help="SQLite results store")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("simulate", help="run the fault-injection grid")
    p.add_argument("--scale", choices=("smoke", "full"), default="smoke")
    p.add_argument("--out", default=str(RAW_DIR))
    p.add_argument("--jobs", type=int, default=1)
    p.set_defaults(fn=cmd_simulate)

    p = sub.add_parser("evaluate", help="score a simulated grid and write the report")
    p.add_argument("--raw", default=str(RAW_DIR))
    p.add_argument("--out", default=str(RESULTS_DIR))
    p.add_argument("--store", help="also write the summaries to this SQLite store")
    p.add_argument("--label")
    p.set_defaults(fn=cmd_evaluate)

    p = sub.add_parser("lake", help="check #35's lake.events, logical and naive walks")
    p.add_argument("--warehouse", required=True)
    p.add_argument("--demo", action="store_true", help="build the demo table first")
    p.add_argument("--rows", type=int, default=spec.LAKE_ROWS)
    p.add_argument("--out", help="write the lake summary CSV here")
    p.add_argument("--store", help="attach the lake summary to the store's latest run")
    p.set_defaults(fn=cmd_lake)

    p = sub.add_parser("gx-parity", help="gate G4: Great Expectations vs own static checks")
    p.add_argument("--seed", type=int, default=spec.SMOKE_SEEDS[0])
    p.set_defaults(fn=cmd_gx_parity)

    p = sub.add_parser("dashboard", help="build the static HTML dashboard from a store")
    p.add_argument("--store", default=str(STORE))
    p.add_argument("--out", default="site")
    p.set_defaults(fn=cmd_dashboard)

    args = parser.parse_args(argv)
    if args.command == "run" and not (args.fixture or args.postgres):
        parser.error("run needs --fixture or --postgres")
    return int(args.fn(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
