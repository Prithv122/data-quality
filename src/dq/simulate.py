"""Run the check suite over simulated streams and collect one long results table.

One row per evaluation. Static checks are evaluated once per batch (`mode == "static"`, k NaN).
Threshold checks are evaluated for both baselines (`frozen`, `rolling`) and every k, from one
metric series. Which rows belong to which arm is decided later, in `evaluate`.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass

import pandas as pd

from dq import adaptive, spec
from dq.checkdefs import CheckSuite, load_checks
from dq.faults import Condition, conditions, inject, verify
from dq.types import BatchContext
from dq.world import Stream, generate_stream

TABLE = "raw.weather_daily"
CLEAN_ID = "clean"
MODES: tuple[str, ...] = ("frozen", "rolling")

RESULT_COLUMNS = ("batch", "check", "family", "mode", "k", "status", "alarm", "observed")
TAG_COLUMNS = ("world", "seed", "stream", "fault", "severity", "onset", "column")


def contexts(stream: Stream, table: str = TABLE) -> list[BatchContext]:
    """One BatchContext per batch, as the batch lands."""
    out: list[BatchContext] = []
    loaded: set[tuple[str, dt.date]] = set()
    latest: dt.datetime | None = None
    for i, df in enumerate(stream.batches):
        batch = i + 1
        if "ingested_at" in df.columns and len(df):
            top = df["ingested_at"].max()
            if not pd.isna(top):
                top = top.to_pydatetime()
                latest = top if latest is None or top > latest else latest
        out.append(
            BatchContext(
                table=table,
                batch=batch,
                batch_df=df,
                loaded_keys=frozenset(loaded),
                parent_keys=stream.parent_keys(batch),
                max_ingested_at=latest,
                check_time=stream.check_times[i],
            )
        )
        # keys become "already loaded" only for later batches
        if "city_id" in df.columns and "date" in df.columns:
            for city, day in zip(df["city_id"], df["date"], strict=True):
                if isinstance(city, str) and isinstance(day, dt.date):
                    loaded.add((city, day))
    return out


def _default_checks() -> tuple[Callable, Callable, Callable]:
    from dq import checks

    return checks.run_static, checks.compute_metric, checks.threshold_result


def run_stream(
    stream: Stream,
    suite: CheckSuite,
    ks: Sequence[float],
    *,
    run_static: Callable | None = None,
    compute_metric: Callable | None = None,
    threshold_result: Callable | None = None,
    contract: object | None = None,
) -> pd.DataFrame:
    """Evaluate every check of `suite` on every batch of `stream`."""
    if run_static is None or compute_metric is None or threshold_result is None:
        d_static, d_metric, d_threshold = _default_checks()
        run_static = run_static or d_static
        compute_metric = compute_metric or d_metric
        threshold_result = threshold_result or d_threshold

    ctxs = contexts(stream, suite.table)
    rows: list[tuple] = []
    nan = float("nan")
    for defn in suite.checks:
        if defn.kind == "static":
            for ctx in ctxs:
                res = run_static(defn, ctx, contract)
                obs = nan if res.observed is None else float(res.observed)
                rows.append(
                    (ctx.batch, defn.id, defn.family, "static", nan, res.status, res.alarm, obs)
                )
            continue
        metrics = [compute_metric(defn, ctx) for ctx in ctxs]
        for mode in MODES:
            for k in ks:
                decisions = adaptive.evaluate_series(metrics, defn.threshold_rule(k, mode))
                for ctx, decision in zip(ctxs, decisions, strict=True):
                    res = threshold_result(defn, ctx.table, ctx.batch, decision)
                    obs = nan if res.observed is None else float(res.observed)
                    rows.append(
                        (
                            ctx.batch,
                            defn.id,
                            defn.family,
                            mode,
                            float(k),
                            res.status,
                            res.alarm,
                            obs,
                        )
                    )
    df = pd.DataFrame(rows, columns=list(RESULT_COLUMNS))
    return df.astype(
        {
            "batch": "int64",
            "check": "str",
            "family": "str",
            "mode": "str",
            "k": "float64",
            "status": "str",
            "alarm": "bool",
            "observed": "float64",
        }
    )


@dataclass
class GridResult:
    results: pd.DataFrame  # run_stream's columns, prefixed by TAG_COLUMNS
    conditions: pd.DataFrame  # id, world, seed, fault, severity, onset, column
    verify_problems: dict[str, list[str]]  # condition id -> G2 problems (empty = clean)


def _tag(
    df: pd.DataFrame,
    world: str,
    seed: int,
    stream_id: str,
    fault: str,
    severity: str,
    onset: float,
    column: str | None,
) -> pd.DataFrame:
    n = len(df)
    tags = pd.DataFrame(
        {
            "world": pd.Series([world] * n, dtype="str"),
            "seed": pd.Series([seed] * n, dtype="int64"),
            "stream": pd.Series([stream_id] * n, dtype="str"),
            "fault": pd.Series([fault] * n, dtype="str"),
            "severity": pd.Series([severity] * n, dtype="str"),
            "onset": pd.Series([onset] * n, dtype="float64"),
            "column": pd.Series([column] * n, dtype="str"),
        }
    )
    return pd.concat([tags, df.reset_index(drop=True)], axis=1)


def _run_task(task: tuple) -> tuple[list[pd.DataFrame], dict[str, list[str]]]:
    """One (world, seed, fault-or-clean) unit of work; top-level so it can run in a process."""
    world, seed, fault, ks, suite, fns = task
    suite = suite if suite is not None else load_checks()
    clean = generate_stream(seed, world)
    frames: list[pd.DataFrame] = []
    problems: dict[str, list[str]] = {}
    if fault is None:
        res = run_stream(clean, suite, ks, **fns)
        frames.append(_tag(res, world, seed, CLEAN_ID, "none", "none", float("nan"), None))
        return frames, problems
    for cond in conditions([seed], [world]):
        if cond.fault != fault:
            continue
        faulted = inject(clean, cond)
        problems[cond.id] = verify(clean, faulted, cond)
        res = run_stream(faulted, suite, ks, **fns)
        frames.append(
            _tag(res, world, seed, cond.id, cond.fault, cond.severity, cond.onset, cond.column)
        )
    return frames, problems


def conditions_frame(conds: Sequence[Condition]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "id": [c.id for c in conds],
            "world": [c.world for c in conds],
            "seed": [c.seed for c in conds],
            "fault": [c.fault for c in conds],
            "severity": [c.severity for c in conds],
            "onset": [c.onset for c in conds],
            "column": [c.column for c in conds],
        }
    )


def run_grid(
    seeds: Sequence[int],
    worlds: Sequence[str],
    ks: Sequence[float],
    *,
    jobs: int = 1,
    suite: CheckSuite | None = None,
    run_static: Callable | None = None,
    compute_metric: Callable | None = None,
    threshold_result: Callable | None = None,
) -> GridResult:
    """Every clean stream and every condition stream for `seeds` x `worlds`.

    Streams are independent, so `jobs > 1` spreads them over processes; results are collected
    in task order and are identical to a single-process run.
    """
    fns = {
        k: v
        for k, v in (
            ("run_static", run_static),
            ("compute_metric", compute_metric),
            ("threshold_result", threshold_result),
        )
        if v is not None
    }
    tasks = []
    for world in worlds:
        for seed in seeds:
            tasks.append((world, seed, None, tuple(ks), suite, fns))
            tasks.extend((world, seed, fault, tuple(ks), suite, fns) for fault in spec.FAULT_TYPES)

    if jobs > 1:
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            outputs = list(pool.map(_run_task, tasks))
    else:
        outputs = [_run_task(t) for t in tasks]

    frames = [f for fs, _ in outputs for f in fs]
    problems: dict[str, list[str]] = {}
    for _, p in outputs:
        problems.update(p)
    conds = conditions(seeds, worlds)
    ordered = {c.id: problems[c.id] for c in conds}
    return GridResult(
        results=pd.concat(frames, ignore_index=True),
        conditions=conditions_frame(conds),
        verify_problems=ordered,
    )
