"""Fault conditions, injection, and the G2 oracle.

`inject` applies one fault from `spec.FAULTS` to a clean stream. `verify` then proves the
difference between the two streams is exactly the labelled fault. It is written separately,
with plain pandas, and imports nothing from `dq.checks`: if a check and the injector shared a
misreading of a rule, a shared helper would hide it.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from dq import spec
from dq.world import Stream, timestamps

TEMPS = ("temp_max_c", "temp_min_c")
ORPHAN_PREFIX = "unregistered-"


@dataclass(frozen=True)
class Condition:
    world: str
    seed: int
    fault: str
    severity: str
    onset: int  # first faulted batch (1-based)
    column: str | None  # null_spike only
    id: str

    @property
    def window(self) -> range:
        return range(self.onset, self.onset + spec.FAULT_WINDOW)


def conditions(seeds: Sequence[int], worlds: Sequence[str]) -> list[Condition]:
    """Every (world, seed, fault, severity). Onset and column depend on (seed, fault, severity)
    only, so W0 and W1 of one seed share them."""
    lo, hi = spec.ONSET_RANGE
    out = []
    for world in worlds:
        for seed in seeds:
            for fi, fault in enumerate(spec.FAULT_TYPES):
                for si, severity in enumerate(spec.SEVERITIES):
                    rng = np.random.default_rng([seed, fi, si])
                    onset = int(rng.integers(lo, hi + 1))
                    column = None
                    if fault == "null_spike":
                        cols = spec.FAULTS[fault][severity]["columns"]
                        column = cols[int(rng.integers(len(cols)))]
                    out.append(
                        Condition(
                            world=world,
                            seed=seed,
                            fault=fault,
                            severity=severity,
                            onset=onset,
                            column=column,
                            id=f"{world}-{seed}-{fault}-{severity}",
                        )
                    )
    return out


def _indices(cond: Condition) -> tuple[int, int]:
    return spec.FAULT_TYPES.index(cond.fault), spec.SEVERITIES.index(cond.severity)


def _rng(cond: Condition, batch: int) -> np.random.Generator:
    fi, si = _indices(cond)
    return np.random.default_rng([cond.seed, fi, si, batch])


def _row_count(params: dict[str, object], n: int) -> int:
    """Rows a fault touches in a batch of n: an exact `rows`, else ceil(fraction * n)."""
    if "rows" in params:
        return min(int(params["rows"]), n)  # type: ignore[call-overload]
    # round() first: 0.1 * 30 is 3.0000000000000004 and would ceil to 4
    return min(math.ceil(round(float(params["fraction"]) * n, 9)), n)  # type: ignore[arg-type]


def _pick(rng: np.random.Generator, pool: np.ndarray, k: int) -> np.ndarray:
    k = min(k, len(pool))
    if k <= 0:
        return np.array([], dtype=int)
    return np.sort(rng.choice(pool, size=k, replace=False))


# --- injection ---------------------------------------------------------------------------


def _schema_drift(batches, stream, cond, params):
    col, op = params["column"], params["op"]
    for b in cond.window:
        df = batches[b - 1]
        if op == "retype":
            df[col] = df[col].astype(str(params["to"]))
        elif op == "rename":
            df = df.rename(columns={col: params["to"]})
        elif op == "drop":
            df = df.drop(columns=[col])
        else:
            raise ValueError(f"unknown schema_drift op {op!r}")
        batches[b - 1] = df


def _freshness_lapse(batches, stream, cond, params):
    missed = int(params["missed"])
    backlog = []
    for b in range(cond.onset, cond.onset + missed):
        backlog.append(batches[b - 1])
        batches[b - 1] = batches[b - 1].iloc[0:0].copy()
    land = cond.onset + missed
    if land > len(batches):
        return  # the backlog never arrives inside the stream
    extra = pd.concat(backlog, ignore_index=True)
    extra["ingested_at"] = timestamps(stream.ingest_times[land - 1], len(extra))
    batches[land - 1] = pd.concat([batches[land - 1], extra], ignore_index=True)


def _volume(batches, stream, cond, params):
    if params["op"] == "drop":
        last = min(cond.onset + spec.FAULT_WINDOW - 1, len(batches))
        present = sorted(stream.parent_keys(last))
        n_keep = round(float(params["keep"]) * len(present))  # type: ignore[arg-type]
        idx = _rng(cond, 0).choice(len(present), size=n_keep, replace=False)
        kept = [present[i] for i in idx]
        for b in cond.window:
            df = batches[b - 1]
            batches[b - 1] = df[df["city_id"].isin(kept)].reset_index(drop=True)
    elif params["op"] == "replay_previous":
        original = [df.copy() for df in batches]
        for b in cond.window:
            prev = original[b - 2].copy()
            prev["ingested_at"] = timestamps(stream.ingest_times[b - 1], len(prev))
            batches[b - 1] = pd.concat([original[b - 1], prev], ignore_index=True)
    else:
        raise ValueError(f"unknown volume op {params['op']!r}")


def _null_spike(batches, stream, cond, params):
    col = cond.column
    for b in cond.window:
        df = batches[b - 1]
        nonnull = np.flatnonzero(df[col].notna().to_numpy())
        pick = _pick(_rng(cond, b), nonnull, _row_count(params, len(df)))
        values = df[col].to_numpy(dtype="float64", copy=True)
        values[pick] = np.nan
        df[col] = values


def _duplicates(batches, stream, cond, params):
    for b in cond.window:
        df = batches[b - 1]
        pick = _pick(_rng(cond, b), np.arange(len(df)), _row_count(params, len(df)))
        batches[b - 1] = pd.concat([df, df.iloc[pick]], ignore_index=True)


def _out_of_range(batches, stream, cond, params):
    for b in cond.window:
        df = batches[b - 1]
        rng = _rng(cond, b)
        if params["op"] == "swap_min_max":
            gap = (df["temp_max_c"] - df["temp_min_c"]).to_numpy()
            pick = _pick(rng, np.flatnonzero(gap >= 1.0), int(params["rows"]))  # type: ignore[call-overload]
            hi = df["temp_max_c"].to_numpy(copy=True)
            lo = df["temp_min_c"].to_numpy(copy=True)
            hi[pick], lo[pick] = (
                df["temp_min_c"].to_numpy()[pick],
                df["temp_max_c"].to_numpy()[pick],
            )
            df["temp_max_c"], df["temp_min_c"] = hi, lo
        elif params["op"] == "set_temp_max":
            nonnull = np.flatnonzero(df["temp_max_c"].notna().to_numpy())
            pick = _pick(rng, nonnull, _row_count(params, len(df)))
            hi = df["temp_max_c"].to_numpy(copy=True)
            hi[pick] = float(params["value"])  # type: ignore[arg-type]
            df["temp_max_c"] = hi
        else:
            raise ValueError(f"unknown out_of_range op {params['op']!r}")


def _orphan_fk(batches, stream, cond, params):
    for b in cond.window:
        df = batches[b - 1]
        pick = _pick(_rng(cond, b), np.arange(len(df)), _row_count(params, len(df)))
        if len(pick) == 0:
            continue
        ids = df["city_id"].astype(object).to_numpy(copy=True)
        for n, i in enumerate(pick, start=1):
            ids[i] = f"{ORPHAN_PREFIX}{n:03d}"
        df["city_id"] = pd.Series(ids, index=df.index).astype("str")


def _silent_shift(batches, stream, cond, params):
    for b in cond.window:
        df = batches[b - 1]
        for col in TEMPS:
            df[col] = df[col] + float(params["delta_c"])  # type: ignore[arg-type]


_INJECTORS: dict[str, Callable] = {
    "schema_drift": _schema_drift,
    "freshness_lapse": _freshness_lapse,
    "volume": _volume,
    "null_spike": _null_spike,
    "duplicates": _duplicates,
    "out_of_range": _out_of_range,
    "orphan_fk": _orphan_fk,
    "silent_shift": _silent_shift,
}


def inject(stream: Stream, cond: Condition) -> Stream:
    """A new stream with `cond`'s fault active for its window. The input is never modified."""
    params = spec.FAULTS[cond.fault][cond.severity]
    batches = [df.copy() for df in stream.batches]
    _INJECTORS[cond.fault](batches, stream, cond, params)
    return Stream(
        seed=stream.seed,
        world=stream.world,
        batches=batches,
        cities=stream.cities.copy(),
        ingest_times=list(stream.ingest_times),
        check_times=list(stream.check_times),
    )


# --- the oracle (G2) ---------------------------------------------------------------------


def _same(a: pd.DataFrame, b: pd.DataFrame) -> bool:
    return a.reset_index(drop=True).equals(b.reset_index(drop=True))


def _differs(a: pd.Series, b: pd.Series) -> np.ndarray:
    """Element-wise a != b, with null == null."""
    out = np.zeros(len(a), dtype=bool)
    for i, (u, v) in enumerate(zip(a.astype(object), b.astype(object), strict=True)):
        un, vn = bool(pd.isna(u)), bool(pd.isna(v))
        out[i] = (un != vn) or (not un and u != v)
    return out


def _changed_rows(a: pd.DataFrame, b: pd.DataFrame, cols: Sequence[str]) -> np.ndarray:
    mask = np.zeros(len(a), dtype=bool)
    for c in cols:
        mask |= _differs(a[c].reset_index(drop=True), b[c].reset_index(drop=True))
    return mask


def _n_touched(params: dict[str, object], n: int) -> int:
    if "rows" in params:
        return min(int(params["rows"]), n)  # type: ignore[call-overload]
    return min(math.ceil(round(float(params["fraction"]) * n, 9)), n)  # type: ignore[arg-type]


def _verify_schema(clean, faulted, cond, params):
    out = []
    col, op = params["column"], params["op"]
    for b in cond.window:
        cb, fb = clean.batches[b - 1], faulted.batches[b - 1]
        tag = f"batch {b}"
        if op == "retype":
            if list(fb.columns) != list(cb.columns):
                out.append(f"{tag}: columns changed")
            elif str(fb[col].dtype) != params["to"]:
                out.append(f"{tag}: {col} is {fb[col].dtype}, expected {params['to']}")
            elif not np.array_equal(
                cb[col].to_numpy("float32"), fb[col].to_numpy("float32"), equal_nan=True
            ):
                out.append(f"{tag}: {col} values changed beyond the cast")
            elif not _same(cb.drop(columns=[col]), fb.drop(columns=[col])):
                out.append(f"{tag}: other columns changed")
        elif op == "rename":
            want = [params["to"] if c == col else c for c in cb.columns]
            if list(fb.columns) != want:
                out.append(f"{tag}: columns {list(fb.columns)}, expected {want}")
            elif not _same(cb, fb.rename(columns={params["to"]: col})):
                out.append(f"{tag}: data changed by the rename")
        elif op == "drop":
            want = [c for c in cb.columns if c != col]
            if list(fb.columns) != want:
                out.append(f"{tag}: columns {list(fb.columns)}, expected {want}")
            elif not _same(cb.drop(columns=[col]), fb):
                out.append(f"{tag}: other columns changed by the drop")
    return out


def _verify_freshness(clean, faulted, cond, params):
    out = []
    missed = int(params["missed"])  # type: ignore[call-overload]
    n_batches = len(clean.batches)
    for b in range(cond.onset, cond.onset + missed):
        cb, fb = clean.batches[b - 1], faulted.batches[b - 1]
        if len(fb) != 0:
            out.append(f"batch {b}: expected an empty batch, has {len(fb)} rows")
        if list(fb.columns) != list(cb.columns) or not fb.dtypes.equals(cb.dtypes):
            out.append(f"batch {b}: empty batch lost its columns or dtypes")
    land = cond.onset + missed
    if land > n_batches:
        return out
    cb, fb = clean.batches[land - 1], faulted.batches[land - 1]
    backlog = pd.concat([clean.batches[b - 1] for b in range(cond.onset, land)], ignore_index=True)
    if len(fb) != len(cb) + len(backlog):
        out.append(f"batch {land}: {len(fb)} rows, expected {len(cb) + len(backlog)}")
        return out
    cols = [c for c in cb.columns if c != "ingested_at"]
    own, tail = fb.iloc[: len(cb)], fb.iloc[len(cb) :]
    if not _same(own, cb):
        out.append(f"batch {land}: its own rows changed")
    if not _same(tail[cols], backlog[cols]):
        out.append(f"batch {land}: backlog rows differ from the missed batches")
    if not (tail["ingested_at"] == faulted.ingest_times[land - 1]).all():
        out.append(f"batch {land}: backlog rows do not carry this batch's ingested_at")
    if fb["ingested_at"].dtype != cb["ingested_at"].dtype:
        out.append(f"batch {land}: ingested_at dtype changed")
    return out


def _verify_volume(clean, faulted, cond, params):
    out = []
    if params["op"] == "drop":
        kept: set[str] = set()
        present: set[str] = set()
        for b in cond.window:
            kept |= set(faulted.batches[b - 1]["city_id"])
            present |= set(clean.batches[b - 1]["city_id"])
        for b in cond.window:
            cb, fb = clean.batches[b - 1], faulted.batches[b - 1]
            want = cb[cb["city_id"].isin(list(kept))]
            if not _same(want, fb):
                out.append(f"batch {b}: not the clean rows of the kept stations")
        registered = int((clean.cities["onboard_batch"] <= cond.window[-1]).sum())
        want_n = round(float(params["keep"]) * registered)  # type: ignore[arg-type]
        if not (want_n - 2 <= len(kept) <= want_n):
            out.append(f"{len(kept)} stations kept, expected about {want_n}")
        if len(present) - len(kept) < 1:
            out.append("no station was dropped")
    elif params["op"] == "replay_previous":
        for b in cond.window:
            cb, prev = clean.batches[b - 1], clean.batches[b - 2]
            fb = faulted.batches[b - 1]
            if len(fb) != len(cb) + len(prev):
                out.append(f"batch {b}: {len(fb)} rows, expected {len(cb) + len(prev)}")
                continue
            own, tail = fb.iloc[: len(cb)], fb.iloc[len(cb) :]
            cols = [c for c in cb.columns if c != "ingested_at"]
            if not _same(own, cb):
                out.append(f"batch {b}: its own rows changed")
            if not _same(tail[cols], prev[cols]):
                out.append(f"batch {b}: replayed rows are not the previous batch")
            if not (tail["ingested_at"] == faulted.ingest_times[b - 1]).all():
                out.append(f"batch {b}: replayed rows do not carry this batch's ingested_at")
    return out


def _verify_null_spike(clean, faulted, cond, params):
    out = []
    col = cond.column
    if col not in params["columns"]:  # type: ignore[operator]
        out.append(f"column {col!r} is not one of {params['columns']}")
        return out
    for b in cond.window:
        cb, fb = clean.batches[b - 1], faulted.batches[b - 1]
        if list(fb.columns) != list(cb.columns) or len(fb) != len(cb):
            out.append(f"batch {b}: shape changed")
            continue
        others = [c for c in cb.columns if c != col]
        if not _same(cb[others], fb[others]):
            out.append(f"batch {b}: columns other than {col} changed")
        was, now = cb[col].isna().to_numpy(), fb[col].isna().to_numpy()
        if (was & ~now).any():
            out.append(f"batch {b}: a null in {col} became a value")
        newly = int((~was & now).sum())
        want = min(_n_touched(params, len(cb)), int((~was).sum()))
        if newly != want:
            out.append(f"batch {b}: {newly} new nulls in {col}, expected {want}")
        keep = ~was & ~now
        if (cb[col].to_numpy()[keep] != fb[col].to_numpy()[keep]).any():
            out.append(f"batch {b}: surviving values in {col} changed")
    return out


def _verify_duplicates(clean, faulted, cond, params):
    out = []
    for b in cond.window:
        cb, fb = clean.batches[b - 1], faulted.batches[b - 1]
        k = _n_touched(params, len(cb))
        if len(fb) != len(cb) + k:
            out.append(f"batch {b}: {len(fb)} rows, expected {len(cb) + k}")
            continue
        if not _same(fb.iloc[: len(cb)], cb):
            out.append(f"batch {b}: original rows changed")
        tail = fb.iloc[len(cb) :]
        if tail.duplicated(["city_id", "date"]).any():
            out.append(f"batch {b}: a row was duplicated more than once")
        merged = tail.merge(
            cb, on=["city_id", "date"], how="left", suffixes=("", "_c"), indicator=True
        )
        if (merged["_merge"] != "both").any():
            out.append(f"batch {b}: a duplicate has no original")
            continue
        for c in [c for c in cb.columns if c not in ("city_id", "date")]:
            if _differs(merged[c], merged[c + "_c"]).any():
                out.append(f"batch {b}: a duplicate differs from its original in {c}")
    return out


def _verify_out_of_range(clean, faulted, cond, params):
    out = []
    for b in cond.window:
        cb, fb = clean.batches[b - 1], faulted.batches[b - 1]
        if list(fb.columns) != list(cb.columns) or len(fb) != len(cb):
            out.append(f"batch {b}: shape changed")
            continue
        cols = list(cb.columns)
        changed = _changed_rows(cb, fb, cols)
        if params["op"] == "swap_min_max":
            gap = (cb["temp_max_c"] - cb["temp_min_c"]).to_numpy()
            want = min(int(params["rows"]), int((gap >= 1.0).sum()))  # type: ignore[call-overload]
            if int(changed.sum()) != want:
                out.append(f"batch {b}: {int(changed.sum())} rows changed, expected {want}")
            if _changed_rows(cb, fb, [c for c in cols if c not in TEMPS]).any():
                out.append(f"batch {b}: columns other than the temperatures changed")
            hit = np.flatnonzero(changed)
            if (
                (fb["temp_max_c"].to_numpy()[hit] != cb["temp_min_c"].to_numpy()[hit]).any()
                or (fb["temp_min_c"].to_numpy()[hit] != cb["temp_max_c"].to_numpy()[hit]).any()
                or (gap[hit] < 1.0).any()
            ):
                out.append(f"batch {b}: changed rows are not a swap of min and max")
        else:
            want = min(_n_touched(params, len(cb)), int(cb["temp_max_c"].notna().sum()))
            if int(changed.sum()) != want:
                out.append(f"batch {b}: {int(changed.sum())} rows changed, expected {want}")
            if _changed_rows(cb, fb, [c for c in cols if c != "temp_max_c"]).any():
                out.append(f"batch {b}: columns other than temp_max_c changed")
            hit = np.flatnonzero(changed)
            if (fb["temp_max_c"].to_numpy()[hit] != float(params["value"])).any():  # type: ignore[arg-type]
                out.append(f"batch {b}: changed rows do not hold {params['value']}")
    return out


def _verify_orphan(clean, faulted, cond, params):
    out = []
    registered = set(clean.cities["city_id"])
    for b in cond.window:
        cb, fb = clean.batches[b - 1], faulted.batches[b - 1]
        if list(fb.columns) != list(cb.columns) or len(fb) != len(cb):
            out.append(f"batch {b}: shape changed")
            continue
        cols = list(cb.columns)
        changed = _changed_rows(cb, fb, cols)
        want = _n_touched(params, len(cb))
        if int(changed.sum()) != want:
            out.append(f"batch {b}: {int(changed.sum())} rows changed, expected {want}")
        if _changed_rows(cb, fb, [c for c in cols if c != "city_id"]).any():
            out.append(f"batch {b}: columns other than city_id changed")
        new_ids = fb["city_id"].to_numpy(dtype=object)[changed]
        if any(not str(v).startswith(ORPHAN_PREFIX) or v in registered for v in new_ids):
            out.append(f"batch {b}: a replaced city_id is registered or not an orphan id")
        # every other row must still point at a registered station
        keep = fb["city_id"].to_numpy(dtype=object)[~changed]
        if any(v not in registered for v in keep):
            out.append(f"batch {b}: an untouched row has an unregistered city_id")
    return out


def _verify_silent_shift(clean, faulted, cond, params):
    out = []
    delta = float(params["delta_c"])  # type: ignore[arg-type]
    for b in cond.window:
        cb, fb = clean.batches[b - 1], faulted.batches[b - 1]
        if list(fb.columns) != list(cb.columns) or len(fb) != len(cb):
            out.append(f"batch {b}: shape changed")
            continue
        others = [c for c in cb.columns if c not in TEMPS]
        if not _same(cb[others], fb[others]):
            out.append(f"batch {b}: columns other than the temperatures changed")
        for col in TEMPS:
            want = cb[col].to_numpy("float64") + delta
            if not np.allclose(
                fb[col].to_numpy("float64"), want, rtol=0, atol=1e-9, equal_nan=True
            ):
                out.append(f"batch {b}: {col} is not the clean value + {delta:g}")
    return out


_VERIFIERS: dict[str, Callable] = {
    "schema_drift": _verify_schema,
    "freshness_lapse": _verify_freshness,
    "volume": _verify_volume,
    "null_spike": _verify_null_spike,
    "duplicates": _verify_duplicates,
    "out_of_range": _verify_out_of_range,
    "orphan_fk": _verify_orphan,
    "silent_shift": _verify_silent_shift,
}


def verify(clean: Stream, faulted: Stream, cond: Condition) -> list[str]:
    """Problems found comparing a faulted stream with its clean twin; empty = exactly the fault."""
    params = spec.FAULTS[cond.fault][cond.severity]
    n = len(clean.batches)
    if len(faulted.batches) != n:
        return [f"{len(faulted.batches)} batches, expected {n}"]
    out = []
    if not _same(clean.cities, faulted.cities):
        out.append("cities changed")
    if clean.ingest_times != faulted.ingest_times or clean.check_times != faulted.check_times:
        out.append("batch times changed")

    if cond.fault == "freshness_lapse":
        last = min(cond.onset + int(params["missed"]), n)  # type: ignore[call-overload]
    else:
        last = min(cond.onset + spec.FAULT_WINDOW - 1, n)
    for b in range(1, n + 1):
        if not (cond.onset <= b <= last) and not _same(
            clean.batches[b - 1], faulted.batches[b - 1]
        ):
            out.append(f"batch {b}: differs from the clean twin outside the fault window")

    out += [f"{cond.id} {p}" for p in _VERIFIERS[cond.fault](clean, faulted, cond, params)]
    return out
