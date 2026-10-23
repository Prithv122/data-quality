"""Pure functions from the long results table to the summary tables and the verdicts.

Nothing here runs a check. Arms are views of the results table: S0 is the static checks that
list S0; S1 is its static checks plus the frozen-baseline threshold rows; A is its static
checks plus the rolling-baseline rows. Threshold rows carry k, static rows do not.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
import pandas as pd

from dq import spec, summary
from dq.checkdefs import CheckSuite, load_checks
from dq.simulate import CLEAN_ID, GridResult
from dq.types import FAMILIES

ARM_MODE = {"S1": "frozen", "A": "rolling"}
THRESHOLD_ARMS = ("S1", "A")
_TWIN_KEYS = ["world", "seed", "check", "mode", "kk", "batch"]
_CELL = ["world", "fault", "severity"]


# --- small helpers -----------------------------------------------------------------------


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for k successes in n trials (95% by default)."""
    if n <= 0:
        return (math.nan, math.nan)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    lo = 0.0 if k == 0 else max(0.0, centre - half)
    hi = 1.0 if k == n else min(1.0, centre + half)
    return (lo, hi)


def result_ks(results: pd.DataFrame) -> list[float]:
    """The k values the threshold rows were computed for (K_DEFAULT if there are none)."""
    ks = sorted(results.loc[results["mode"] != "static", "k"].dropna().unique())
    return [float(k) for k in ks] or [spec.K_DEFAULT]


def arm_k_pairs(ks: Sequence[float]) -> list[tuple[str, float]]:
    return [("S0", math.nan)] + [(arm, float(k)) for arm in THRESHOLD_ARMS for k in ks]


def _arm_mask(df: pd.DataFrame, arm: str, k: float, suite: CheckSuite) -> pd.Series:
    """Rows of `df` that belong to `arm` (at threshold k for S1 and A)."""
    in_arm = df["check"].isin({c.id for c in suite.checks if arm in c.arms})
    static = df["mode"] == "static"
    if arm == "S0":
        return in_arm & static
    return in_arm & (static | ((df["mode"] == ARM_MODE[arm]) & (df["k"] == k)))


def arm_view(
    results: pd.DataFrame, arm: str, k: float = math.nan, suite: CheckSuite | None = None
) -> pd.DataFrame:
    """The rows of `results` one arm sees, labelled with the arm and k."""
    suite = suite or load_checks()
    view = results.loc[_arm_mask(results, arm, k, suite)].copy()
    view["arm"] = arm
    view["k"] = math.nan if arm == "S0" else float(k)
    return view


def _sorted(df: pd.DataFrame, suite: CheckSuite | None = None) -> pd.DataFrame:
    """Stable, human order: worlds, arms, k, faults, severities, families, checks."""
    orders: dict[str, list] = {
        "world": list(spec.WORLDS),
        "arm": list(spec.ARMS),
        "fault": list(spec.FAULT_TYPES),
        "severity": list(spec.SEVERITIES),
        "family": list(FAMILIES),
    }
    if suite is not None:
        orders["check"] = [c.id for c in suite.checks]
    keys, tmp = [], df.copy()
    for col in ("world", "arm", "k", "fault", "severity", "family", "check"):
        if col not in tmp.columns:
            continue
        if col == "k":
            tmp["_r_k"] = tmp["k"].fillna(-1.0)
        elif col in orders:
            rank = {v: i for i, v in enumerate(orders[col])}
            tmp[f"_r_{col}"] = tmp[col].map(rank).fillna(len(rank)).astype(int)
        else:
            continue
        keys.append(f"_r_{col}")
    out = tmp.sort_values(keys, kind="stable") if keys else tmp
    return out.drop(columns=[c for c in out.columns if c.startswith("_r_")]).reset_index(drop=True)


# --- attribution -------------------------------------------------------------------------


def _alarms_vs_twin(results: pd.DataFrame, window: int = spec.FAULT_WINDOW) -> pd.DataFrame:
    """Alarm rows of faulted streams that the clean twin (same world, seed) does not raise,
    labelled `phase`: before the onset, inside the fault window, or after it."""
    alarms = results.loc[results["alarm"]].copy()
    alarms["kk"] = alarms["k"].fillna(-1.0)
    is_clean = alarms["stream"] == CLEAN_ID
    twin = alarms.loc[is_clean, _TWIN_KEYS].drop_duplicates()
    faulted = alarms.loc[~is_clean].merge(twin, how="left", on=_TWIN_KEYS, indicator=True)
    faulted = faulted.loc[faulted["_merge"] == "left_only"].drop(columns=["_merge", "kk"])
    batch, onset = faulted["batch"], faulted["onset"]
    faulted["phase"] = np.where(
        batch < onset, "before", np.where(batch < onset + window, "window", "after")
    )
    return faulted.reset_index(drop=True)


def attributable(results: pd.DataFrame, window: int = spec.FAULT_WINDOW) -> pd.DataFrame:
    """Alarms inside a fault window that the same check does not raise at the same batch in
    the clean twin (same mode and k for threshold rows)."""
    out = _alarms_vs_twin(results, window)
    return out.loc[out["phase"] == "window"].drop(columns="phase").reset_index(drop=True)


def post_fault(results: pd.DataFrame) -> pd.DataFrame:
    """Twin-free alarms after a fault window closed (baseline contamination)."""
    out = _alarms_vs_twin(results)
    return out.loc[out["phase"] == "after"].drop(columns="phase").reset_index(drop=True)


# --- detection ---------------------------------------------------------------------------


def detection_table(
    att: pd.DataFrame, conds: pd.DataFrame, suite: CheckSuite, ks: Sequence[float]
) -> pd.DataFrame:
    """One row per (arm, k, condition): detected, and the first attributable batch."""
    base = conds.rename(columns={"id": "stream"})[
        ["stream", "world", "seed", "fault", "severity", "onset"]
    ]
    parts = []
    for arm, k in arm_k_pairs(ks):
        sub = att.loc[_arm_mask(att, arm, k, suite)]
        first = sub.groupby("stream")["batch"].min()
        part = base.copy()
        part.insert(1, "arm", arm)
        part.insert(2, "k", k)
        part["first_batch"] = part["stream"].map(first).astype("float64")
        part["detected"] = part["first_batch"].notna()
        parts.append(part)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def recall_table(det: pd.DataFrame) -> pd.DataFrame:
    g = det.groupby(["world", "arm", "k", "fault", "severity"], dropna=False, sort=False)
    out = g.agg(detected=("detected", "sum"), n=("detected", "size")).reset_index()
    out["detected"] = out["detected"].astype(int)
    out["recall"] = out["detected"] / out["n"]
    bounds = [wilson(int(d), int(n)) for d, n in zip(out["detected"], out["n"], strict=True)]
    out["ci_low"] = [b[0] for b in bounds]
    out["ci_high"] = [b[1] for b in bounds]
    return _sorted(out)


def family_counts(
    att: pd.DataFrame, conds: pd.DataFrame, suite: CheckSuite, arm: str, k: float
) -> pd.DataFrame:
    """Per (world, fault, severity, family): seeds with an attributable alarm, and seeds."""
    sub = att.loc[_arm_mask(att, arm, k, suite), ["stream", "family"]].drop_duplicates()
    sub = sub.assign(hit=True)
    full = conds.rename(columns={"id": "stream"})[["stream", *_CELL]].merge(
        pd.DataFrame({"family": list(FAMILIES)}), how="cross"
    )
    full = full.merge(sub, how="left", on=["stream", "family"])
    full["hit"] = full["hit"].notna()
    g = full.groupby([*_CELL, "family"], sort=False)
    out = g.agg(detected=("hit", "sum"), n=("hit", "size")).reset_index()
    out["detected"] = out["detected"].astype(int)
    return out


def recall_by_family_table(
    att: pd.DataFrame, conds: pd.DataFrame, suite: CheckSuite, ks: Sequence[float]
) -> pd.DataFrame:
    parts = []
    for arm, k in arm_k_pairs(ks):
        part = family_counts(att, conds, suite, arm, k)
        part.insert(1, "arm", arm)
        part.insert(2, "k", k)
        parts.append(part)
    return (
        _sorted(pd.concat(parts, ignore_index=True)) if parts else summary.empty("recall_by_family")
    )


def ttd_table(det: pd.DataFrame) -> pd.DataFrame:
    d = det.copy()
    d["ttd"] = np.where(d["detected"], d["first_batch"] - d["onset"], np.nan)
    g = d.groupby(["world", "arm", "k", "fault", "severity"], dropna=False, sort=False)
    out = g.agg(
        n_detected=("detected", "sum"), ttd_median=("ttd", "median"), ttd_max=("ttd", "max")
    ).reset_index()
    out["n_detected"] = out["n_detected"].astype(int)
    return _sorted(out)


# --- false alarms, operating points ------------------------------------------------------


def false_alarm_table(
    results: pd.DataFrame, suite: CheckSuite, ks: Sequence[float] | None = None
) -> pd.DataFrame:
    """Alarms per 100 batch-checks on the clean streams, scored batches only.

    One row per (world, arm, k, check). A skipped result is a batch-check that did not alarm.
    Static checks of S1 and A repeat at every k so each (arm, k) is a complete row set.
    """
    ks = list(ks) if ks is not None else result_ks(results)
    clean = results.loc[(results["stream"] == CLEAN_ID) & (results["batch"] >= spec.EVAL_FIRST)]
    parts = []
    for arm, k in arm_k_pairs(ks):
        sub = clean.loc[_arm_mask(clean, arm, k, suite)]
        g = sub.groupby(["world", "check", "family"], sort=False)
        part = g.agg(alarms=("alarm", "sum"), batch_checks=("alarm", "size")).reset_index()
        part.insert(1, "arm", arm)
        part.insert(2, "k", k)
        parts.append(part)
    if not parts:
        return summary.empty("false_alarms")
    out = pd.concat(parts, ignore_index=True)
    out["alarms"] = out["alarms"].astype(int)
    out["per_100"] = 100.0 * out["alarms"] / out["batch_checks"]
    return _sorted(out, suite)


def operating_points(fa: pd.DataFrame) -> pd.DataFrame:
    """Per world and arm (S1, A): the smallest k in K_GRID whose clean false-alarm rate over
    OPERATING_POINT_CHECKS is within the target; k = largest grid value if none is."""
    rows = []
    for world in [w for w in spec.WORLDS if w in set(fa["world"])]:
        for arm in THRESHOLD_ARMS:
            sub = fa.loc[
                (fa["world"] == world)
                & (fa["arm"] == arm)
                & fa["check"].isin(spec.OPERATING_POINT_CHECKS)
            ]
            rate = {}
            for k, g in sub.groupby("k"):
                if g["batch_checks"].sum() > 0:
                    rate[float(k)] = 100.0 * g["alarms"].sum() / g["batch_checks"].sum()
            chosen = next(
                (k for k in spec.K_GRID if k in rate and rate[k] <= spec.FA_TARGET_PER_100), None
            )
            reached = chosen is not None
            if chosen is None:
                chosen = spec.K_GRID[-1]
            rows.append(
                {
                    "world": world,
                    "arm": arm,
                    "k": float(chosen),
                    "fa_per_100": rate.get(float(chosen), math.nan),
                    "reached_target": reached,
                }
            )
    if not rows:
        return summary.empty("operating_points")
    return pd.DataFrame(rows)


# --- verdicts ----------------------------------------------------------------------------


def _detected_streams(det: pd.DataFrame, world: str, arm: str, k: float) -> set[str]:
    k_ok = det["k"].isna() if math.isnan(k) else det["k"] == k
    sub = det.loc[(det["world"] == world) & (det["arm"] == arm) & k_ok & det["detected"]]
    return set(sub["stream"])


def h1_verdict(det: pd.DataFrame, ops: pd.DataFrame) -> pd.DataFrame:
    """H1: at matched false alarms, rolling (A) detects more small-severity volume and null
    conditions than frozen (S1). a = A detects and S1 misses, s = the reverse; supported iff a > s.
    World W1 decides; other worlds are reported."""
    rows = []
    for world in [w for w in spec.WORLDS if w in set(ops["world"])]:
        op = ops.loc[ops["world"] == world].set_index("arm")
        if not {"S1", "A"} <= set(op.index):
            continue
        k_s1, k_a = float(op.loc["S1", "k"]), float(op.loc["A", "k"])
        cells = det.loc[(det["world"] == world) & (det["arm"] == "A") & (det["k"] == k_a)]
        in_cells = pd.Series(False, index=cells.index)
        for fault, severity in spec.H1_CELLS:
            in_cells |= (cells["fault"] == fault) & (cells["severity"] == severity)
        streams = set(cells.loc[in_cells, "stream"])
        det_a = _detected_streams(det, world, "A", k_a) & streams
        det_s1 = _detected_streams(det, world, "S1", k_s1) & streams
        a, s = len(det_a - det_s1), len(det_s1 - det_a)
        reached = (
            f"target {spec.FA_TARGET_PER_100:g}/100 reached by both"
            if bool(op["reached_target"].all())
            else "target not reached by "
            + ", ".join(str(a_) for a_ in op.index[~op["reached_target"].astype(bool)])
            + f" (k = {spec.K_GRID[-1]:g})"
        )
        detail = (
            f"a = {a} (A detects, S1 misses), s = {s} (S1 detects, A misses) over "
            f"{len(streams)} small-severity conditions; k_A = {k_a:g}, k_S1 = {k_s1:g}; "
            f"clean FA/100: A {op.loc['A', 'fa_per_100']:.2f}, S1 {op.loc['S1', 'fa_per_100']:.2f} "
            f"({reached})"
        )
        if world == "W1":
            verdict = "supported" if a > s else "not supported"
        else:
            verdict = "reported"
            detail += "; sensitivity world, does not decide H1"
        rows.append({"hypothesis": "H1", "world": world, "verdict": verdict, "detail": detail})
    return pd.DataFrame(rows, columns=list(summary.SUMMARY_COLUMNS["verdicts"]))


def h2_verdict(
    det: pd.DataFrame, att: pd.DataFrame, suite: CheckSuite, k: float = spec.K_DEFAULT
) -> pd.DataFrame:
    """H2: silent_shift is never detected (0 of 15 per arm per world), at k = K_DEFAULT."""
    rows = []
    shift = det.loc[det["fault"] == "silent_shift"]
    for world in [w for w in spec.WORLDS if w in set(shift["world"])]:
        parts, total, fired = [], 0, []
        for arm, kk in (("S0", math.nan), ("S1", k), ("A", k)):
            k_ok = shift["k"].isna() if math.isnan(kk) else shift["k"] == kk
            sub = shift.loc[(shift["world"] == world) & (shift["arm"] == arm) & k_ok]
            d, n = int(sub["detected"].sum()), len(sub)
            total += d
            parts.append(f"{arm} {d}/{n}")
            if d:
                hits = att.loc[
                    (att["world"] == world)
                    & (att["fault"] == "silent_shift")
                    & _arm_mask(att, arm, kk, suite)
                ]
                names = hits.groupby("check")["stream"].nunique()
                fired.append(
                    f"{arm}: " + ", ".join(f"{c} ({int(v)} conditions)" for c, v in names.items())
                )
        detail = f"detected / conditions at k = {k:g}: " + ", ".join(parts)
        if fired:
            detail += "; fired: " + "; ".join(fired)
        rows.append(
            {
                "hypothesis": "H2",
                "world": world,
                "verdict": "supported" if total == 0 else "not supported",
                "detail": detail,
            }
        )
    return pd.DataFrame(rows, columns=list(summary.SUMMARY_COLUMNS["verdicts"]))


def expected_mismatches(
    att: pd.DataFrame,
    conds: pd.DataFrame,
    suite: CheckSuite,
    *,
    world: str = "W1",
    k: float = spec.K_DEFAULT,
    majority: int = spec.MAJORITY,
) -> pd.DataFrame:
    """H3: the cells where the pre-registered family prediction and the observation differ.
    A family detects a cell when at least `majority` seeds had an attributable alarm in it."""
    cond_w = conds.loc[conds["world"] == world]
    rows = []
    if len(cond_w):
        att_w = att.loc[att["world"] == world]
        for arm in spec.ARMS:
            counts = family_counts(att_w, cond_w, suite, arm, math.nan if arm == "S0" else k)
            for r in counts.itertuples(index=False):
                predicted = r.family in spec.EXPECTED[arm][r.fault][r.severity]
                observed = r.detected >= majority
                if predicted != observed:
                    rows.append(
                        {
                            "arm": arm,
                            "fault": r.fault,
                            "severity": r.severity,
                            "family": r.family,
                            "predicted": predicted,
                            "seeds_detected": int(r.detected),
                            "n": int(r.n),
                        }
                    )
    cols = list(summary.SUMMARY_COLUMNS["mismatches"])
    return _sorted(pd.DataFrame(rows, columns=cols)) if rows else summary.empty("mismatches")


def post_fault_table(
    results: pd.DataFrame, conds: pd.DataFrame, suite: CheckSuite, ks: Sequence[float]
) -> pd.DataFrame:
    post = post_fault(results)
    cells = conds[_CELL].drop_duplicates()
    parts = []
    for arm, k in arm_k_pairs(ks):
        sub = post.loc[_arm_mask(post, arm, k, suite)]
        counts = sub.groupby(_CELL, sort=False).size().rename("alarms").reset_index()
        part = cells.merge(counts, how="left", on=_CELL)
        part["alarms"] = part["alarms"].fillna(0).astype(int)
        part.insert(1, "arm", arm)
        part.insert(2, "k", k)
        parts.append(part)
    return (
        _sorted(pd.concat(parts, ignore_index=True))
        if parts
        else summary.empty("post_fault_alarms")
    )


# --- gates -------------------------------------------------------------------------------


def gate_g6(results: pd.DataFrame) -> list[str]:
    """G6: a clean stream scored against itself has zero attributable alarms."""
    clean = results.loc[results["stream"] == CLEAN_ID]
    twin = clean.assign(
        stream="clean-self", fault="self", severity="self", onset=float(spec.EVAL_FIRST)
    )
    att = attributable(pd.concat([clean, twin], ignore_index=True), window=spec.N_BATCHES)
    return [
        f"{r.world} seed {r.seed}: {r.check} ({r.mode}, k={r.k}) batch {r.batch}"
        for r in att.itertuples(index=False)
    ]


def gate_attribution_before_onset(results: pd.DataFrame) -> list[str]:
    """No twin-free alarm can precede an onset: a faulted stream equals its twin until then."""
    out = _alarms_vs_twin(results)
    out = out.loc[out["phase"] == "before"]
    return [
        f"{r.stream}: {r.check} ({r.mode}, k={r.k}) alarms at batch {r.batch}, "
        f"before onset {r.onset:g}"
        for r in out.itertuples(index=False)
    ]


def gate_s0_silent_on_clean(results: pd.DataFrame, suite: CheckSuite) -> list[str]:
    """Distrust signal: an S0 check alarming on a clean stream means a generator or check bug."""
    clean = results.loc[(results["stream"] == CLEAN_ID) & results["alarm"]]
    hits = clean.loc[_arm_mask(clean, "S0", math.nan, suite)]
    return [
        f"{r.world} seed {r.seed}: {r.check} alarms at batch {r.batch}"
        for r in hits.itertuples(index=False)
    ]


# --- everything --------------------------------------------------------------------------


def evaluate_tables(
    results: pd.DataFrame,
    conds: pd.DataFrame,
    suite: CheckSuite | None = None,
    ks: Sequence[float] | None = None,
) -> dict[str, pd.DataFrame]:
    suite = suite or load_checks()
    ks = list(ks) if ks is not None else result_ks(results)
    att = attributable(results)
    det = detection_table(att, conds, suite, ks)
    fa = false_alarm_table(results, suite, ks)
    ops = operating_points(fa) if len(fa) else summary.empty("operating_points")
    verdicts = pd.concat([h1_verdict(det, ops), h2_verdict(det, att, suite)], ignore_index=True)
    tables = {
        "recall": recall_table(det),
        "recall_by_family": recall_by_family_table(att, conds, suite, ks),
        "false_alarms": fa,
        "ttd": ttd_table(det),
        "operating_points": ops,
        "verdicts": verdicts,
        "mismatches": expected_mismatches(att, conds, suite),
        "post_fault_alarms": post_fault_table(results, conds, suite, ks),
    }
    return {name: summary.validate(name, df).reset_index(drop=True) for name, df in tables.items()}


def evaluate_grid(
    grid: GridResult, suite: CheckSuite | None = None, ks: Sequence[float] | None = None
) -> dict[str, pd.DataFrame]:
    """Every summary table of a finished grid, keyed by its name in `summary.SUMMARY_COLUMNS`."""
    return evaluate_tables(grid.results, grid.conditions, suite, ks)
