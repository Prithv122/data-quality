"""The lake source: data quality checks over `35-lakehouse`'s Iceberg table, one result per version.

The table is read only through `lakehouse.contract` (`snapshots`, `to_arrow`, `open_table`).
That contract lists a compaction as two snapshots (a DELETE with 0 records, then the APPEND with
the rewritten rows), so a naive walk over `snapshots()` sees "table emptied, then refilled" and a
fresh commit that carries no new data. `logical_versions` folds such a pair into one *maintenance*
version; `naive_versions` keeps every snapshot, as the trap to compare against.

`lakehouse` is imported inside the functions that need it, so importing this module works
without the optional `lake` dependency group.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc

from dq import spec
from dq.adaptive import Decision, ThresholdRule, evaluate_series
from dq.summary import SUMMARY_COLUMNS
from dq.types import CheckResult, MetricValue

Kind = Literal["append", "overwrite", "maintenance", "empty"]
Walk = Literal["logical", "naive"]
Snap = tuple[int, dt.datetime, int]  # (snapshot_id, committed_at UTC, total_records)

MAINTENANCE: Kind = "maintenance"

# --- versions ----------------------------------------------------------------------------


@dataclass(frozen=True)
class LogicalVersion:
    index: int  # 0-based position in the walk
    snapshot_id: int  # the snapshot that represents the version (the APPEND half of a pair)
    committed_at: dt.datetime
    total_records: int
    kind: Kind
    folded: tuple[int, ...] = ()  # snapshot ids folded into this version (the DELETE half)
    # commit time of the last version that brought data; a maintenance version inherits the
    # previous version's value, so a compaction never refreshes freshness
    data_committed_at: dt.datetime | None = None


def logical_versions(
    snaps: Sequence[Snap],
    same_rows: Callable[[int, int], bool],
    pair_gap_seconds: float = spec.PAIR_GAP_SECONDS,
) -> list[LogicalVersion]:
    """Fold overwrite pairs and classify every version.

    A 0-record snapshot followed by another snapshot within `pair_gap_seconds` is the first half
    of one overwrite commit and is folded into the second. A folded pair is maintenance when its
    rows equal the previous version's (`same_rows`), else an overwrite (truncate = overwrite to 0
    rows). An unpaired 0-record snapshot is a real empty version. An unfolded snapshot is an
    append when it holds more records than the previous version, else an overwrite.

    `same_rows` is only consulted when the record counts match: different counts cannot be the
    same rows, and the callback reads data.
    """
    out: list[LogicalVersion] = []
    i, n = 0, len(snaps)
    while i < n:
        sid, ts, total = snaps[i]
        folded: tuple[int, ...] = ()
        paired = False
        if total == 0 and i + 1 < n and (snaps[i + 1][1] - ts).total_seconds() <= pair_gap_seconds:
            folded = (sid,)
            sid, ts, total = snaps[i + 1]
            paired = True
            i += 2
        else:
            i += 1

        prev = out[-1] if out else None
        kind: Kind
        if paired:
            if prev is None:
                kind = "append" if total > 0 else "empty"
            elif total == prev.total_records and same_rows(prev.snapshot_id, sid):
                kind = "maintenance"
            else:
                kind = "overwrite"
        elif total == 0:
            kind = "empty"
        elif prev is None or total > prev.total_records:
            kind = "append"
        else:
            kind = "overwrite"

        data_ts = prev.data_committed_at if kind == "maintenance" and prev else ts
        out.append(LogicalVersion(len(out), sid, ts, total, kind, folded, data_ts))
    return out


def naive_versions(snaps: Sequence[Snap]) -> list[LogicalVersion]:
    """Every snapshot is its own version, and every commit counts as fresh data."""
    out: list[LogicalVersion] = []
    prev_total = 0
    for sid, ts, total in snaps:
        kind: Kind = "empty" if total == 0 else ("append" if total > prev_total else "overwrite")
        out.append(LogicalVersion(len(out), sid, ts, total, kind, (), ts))
        prev_total = total
    return out


# --- reading snapshots -------------------------------------------------------------------


def _fingerprint(t: pa.Table) -> tuple[int, tuple[str, ...], str]:
    """Row count, column names and a hash of all rows sorted by event_id (then every column)."""
    cols = sorted(t.column_names)
    df = t.select(cols).to_pandas()
    order = (["event_id"] if "event_id" in cols else []) + [c for c in cols if c != "event_id"]
    df = df.sort_values(order, kind="stable").reset_index(drop=True)
    digest = hashlib.sha256(pd.util.hash_pandas_object(df, index=False).to_numpy().tobytes())
    return t.num_rows, tuple(cols), digest.hexdigest()


class _Reader:
    """Snapshot contents and fingerprints, each read once."""

    def __init__(self, read: Callable[[int], pa.Table]) -> None:
        self._read = read
        self._content: dict[int, pa.Table] = {}
        self._fp: dict[int, tuple[int, tuple[str, ...], str]] = {}

    def content(self, snapshot_id: int) -> pa.Table:
        if snapshot_id not in self._content:
            self._content[snapshot_id] = self._read(snapshot_id)
        return self._content[snapshot_id]

    def same_rows(self, a: int, b: int) -> bool:
        for sid in (a, b):
            if sid not in self._fp:
                self._fp[sid] = _fingerprint(self.content(sid))
        return self._fp[a] == self._fp[b]


def _table_reader(table: Any) -> _Reader:
    from lakehouse import contract

    return _Reader(lambda sid: contract.to_arrow(table, sid))


def same_rows_from_table(table: Any) -> Callable[[int, int], bool]:
    """The real `same_rows`: equal row sets (same event_id multiset, same values everywhere)."""
    return _table_reader(table).same_rows


def batch_frame(version: LogicalVersion, prev: LogicalVersion | None, reader: _Reader) -> pa.Table:
    """The rows a version delivers: new event_ids for an append, everything otherwise."""
    cur = reader.content(version.snapshot_id)
    if version.kind != "append" or prev is None:
        return cur
    before = reader.content(prev.snapshot_id)
    if "event_id" not in cur.column_names or "event_id" not in before.column_names:
        return cur
    seen = pc.is_in(cur["event_id"], value_set=before["event_id"])
    return cur.filter(pc.invert(seen))


# --- checks ------------------------------------------------------------------------------

TABLE = spec.LAKE_TABLE
_ROLLING = {"mode": "rolling", "warmup": 3, "window": 14, "min_baseline": 3}


def _norm_type(t: pa.DataType) -> str:
    """Iceberg reads strings back as large_string and tz as UTC; the contract means the same."""
    if pa.types.is_large_string(t):
        return "string"
    if pa.types.is_large_binary(t):
        return "binary"
    if pa.types.is_timestamp(t):
        return f"timestamp[{t.unit}{', tz' if t.tz else ''}]"
    return str(t)


def _schema_result(v: LogicalVersion, content: pa.Table) -> CheckResult:
    from lakehouse.spec import ARROW_SCHEMA_V1

    want = {f.name: _norm_type(f.type) for f in ARROW_SCHEMA_V1}
    have = {f.name: _norm_type(f.type) for f in content.schema}
    missing = [c for c in want if c not in have]
    retyped = [f"{c}: {have[c]} != {want[c]}" for c in want if c in have and have[c] != want[c]]
    extra = [c for c in have if c not in want]
    broken = [f"missing {c}" for c in missing] + [f"retyped {r}" for r in retyped]
    if broken:
        status, detail = "fail", "; ".join(broken + [f"extra {c}" for c in extra])
    elif extra:
        status, detail = "warn", "additive evolution, extra columns: " + ", ".join(extra)
    else:
        status, detail = "pass", ""
    return CheckResult(
        "schema.contract_lake", "schema", TABLE, v.index, status,
        float(len(broken) + len(extra)), "equals ARROW_SCHEMA_V1", detail, v.snapshot_id,
    )  # fmt: skip


def _unique_result(v: LogicalVersion, content: pa.Table) -> CheckResult:
    rule = "0 duplicate event_id"
    if "event_id" not in content.column_names:
        return CheckResult(
            "unique.event_id", "unique", TABLE, v.index, "error", None, rule,
            "column event_id missing", v.snapshot_id,
        )  # fmt: skip
    col = content["event_id"]
    dups = len(col) - len(pc.unique(col))
    return CheckResult(
        "unique.event_id", "unique", TABLE, v.index, "fail" if dups else "pass",
        float(dups), rule, "", v.snapshot_id,
    )  # fmt: skip


def _range_result(v: LogicalVersion, frame: pa.Table) -> CheckResult:
    rule = "amount >= 0 (nulls pass)"
    if "amount" not in frame.column_names:
        return CheckResult(
            "accepted_range.amount", "range", TABLE, v.index, "error", None, rule,
            "column amount missing", v.snapshot_id,
        )  # fmt: skip
    bad = pc.sum(pc.fill_null(pc.less(frame["amount"], 0), False)).as_py() or 0
    return CheckResult(
        "accepted_range.amount", "range", TABLE, v.index, "fail" if bad else "pass",
        float(bad), rule, "", v.snapshot_id,
    )  # fmt: skip


def _null_rate_metric(frame: pa.Table) -> MetricValue:
    if "amount" not in frame.column_names or "event_name" not in frame.column_names:
        return MetricValue(None, "error", "column amount or event_name missing")
    purchase = pc.equal(frame["event_name"], "purchase")
    n = pc.sum(purchase).as_py() or 0
    if n == 0:
        return MetricValue(None, "skipped", "no purchases in the batch")
    nulls = pc.sum(pc.and_(purchase, pc.is_null(frame["amount"]))).as_py() or 0
    return MetricValue(nulls / n)


def _as_result(check: str, family: Any, v: LogicalVersion, d: Decision) -> CheckResult:
    return CheckResult(
        check, family, TABLE, v.index, d.status, d.observed, d.expected or d.detail,
        d.detail, v.snapshot_id,
    )  # fmt: skip


def _freshness_result(
    v: LogicalVersion, check_time: dt.datetime, max_age_hours: float
) -> CheckResult:
    assert v.data_committed_at is not None
    lag = (check_time - v.data_committed_at).total_seconds() / 3600
    status = "fail" if lag > max_age_hours else "pass"
    detail = f"last data commit {v.data_committed_at.isoformat()}"
    return CheckResult(
        "freshness.last_data_commit", "freshness", TABLE, v.index, status, lag,
        f"<= {max_age_hours:g} h", detail, v.snapshot_id,
    )  # fmt: skip


def run_lake_checks(
    table: Any,
    walk: Walk = "logical",
    check_time: dt.datetime | None = None,
    k: float = spec.LAKE_K,
    max_age_hours: float = 24.0,
    snaps: Sequence[Snap] | None = None,
    read: Callable[[int], pa.Table] | None = None,
) -> list[CheckResult]:
    """Run the lake checks over every version of `table`.

    `walk` picks the version list: "logical" folds compaction pairs, "naive" treats every
    snapshot as a version. `snaps` and `read` replace `contract.snapshots(table)` and
    `contract.to_arrow(table, id)`, so the same logic runs on synthetic snapshots in tests.
    """
    if snaps is None:
        from lakehouse import contract

        snaps = contract.snapshots(table)
    reader = _Reader(read) if read is not None else _table_reader(table)
    check_time = check_time or dt.datetime.now(dt.UTC)
    versions = (
        logical_versions(snaps, reader.same_rows) if walk == "logical" else naive_versions(snaps)
    )
    if not versions:
        return []

    batch_versions = [v for v in versions if v.kind != MAINTENANCE]
    prev_of = {v.index: (versions[v.index - 1] if v.index else None) for v in versions}
    frames = {v.index: batch_frame(v, prev_of[v.index], reader) for v in batch_versions}

    if walk == "logical":
        vol = [float(frames[v.index].num_rows) for v in batch_versions]
    else:
        totals = [0, *(v.total_records for v in versions)]
        vol = [float(v.total_records - totals[v.index]) for v in batch_versions]
    vol_rule = ThresholdRule(
        k=k,
        side="two_sided",
        floor_relative=0.01,
        **_ROLLING,  # type: ignore[arg-type]
    )
    null_rule = ThresholdRule(k=k, side="upper", floor=0.005, **_ROLLING)  # type: ignore[arg-type]
    vol_dec = dict(
        zip(
            (v.index for v in batch_versions),
            evaluate_series([MetricValue(x) for x in vol], vol_rule),
            strict=True,
        )
    )
    null_dec = dict(
        zip(
            (v.index for v in batch_versions),
            evaluate_series(
                [_null_rate_metric(frames[v.index]) for v in batch_versions], null_rule
            ),
            strict=True,
        )
    )

    out: list[CheckResult] = []
    for v in versions:
        content = reader.content(v.snapshot_id)
        out.append(_schema_result(v, content))
        out.append(_unique_result(v, content))
        if v.kind == MAINTENANCE:
            continue
        out.append(_range_result(v, frames[v.index]))
        out.append(_as_result("volume.rows_added", "volume", v, vol_dec[v.index]))
        out.append(_as_result("null_rate.amount_purchase", "nulls", v, null_dec[v.index]))
    out.append(_freshness_result(versions[-1], check_time, max_age_hours))
    return out


def lake_versions(
    table: Any, walk: Walk = "logical", snaps: Sequence[Snap] | None = None,
    read: Callable[[int], pa.Table] | None = None,
) -> list[LogicalVersion]:  # fmt: skip
    """The version list `run_lake_checks` walks, for pairing with its results."""
    if snaps is None:
        from lakehouse import contract

        snaps = contract.snapshots(table)
    if walk == "naive":
        return naive_versions(snaps)
    reader = _Reader(read) if read is not None else _table_reader(table)
    return logical_versions(snaps, reader.same_rows)


def lake_summary(
    results: Sequence[CheckResult], versions: Sequence[LogicalVersion], walk: Walk
) -> pd.DataFrame:
    """One row per (version, check) in the `summary.SUMMARY_COLUMNS["lake"]` layout."""
    kinds = {v.index: v.kind for v in versions}
    rows = [
        {
            "walk": walk,
            "version": r.batch,
            "snapshot_id": r.snapshot_id,
            "kind": kinds.get(r.batch if r.batch is not None else -1),
            "check": r.check,
            "family": r.family,
            "status": r.status,
            "observed": r.observed,
            "expected": r.expected,
            "detail": r.detail,
        }
        for r in results
    ]
    return pd.DataFrame(rows, columns=list(SUMMARY_COLUMNS["lake"]))


# --- demo table --------------------------------------------------------------------------

DEMO_SEED = 3650
DEMO_DUPLICATE_IDS = 50
DEMO_NEGATIVE_ROWS = 20
DEMO_NULL_SHARE = 0.30


def build_demo(warehouse: str | Path, rows: int = spec.LAKE_ROWS) -> Any:
    """#35's canonical `lake.events`, compacted, then clean and faulted appends.

    With n = rows // 10 (the size of one canonical append), the versions are:

    0-9    the ten canonical appends of n rows each
    10     compaction (L1): a DELETE with 0 records, then an APPEND with all rows (maintenance)
    11-12  two clean appends of n rows
    13     (a) n rows where 30% of the purchases have a null `amount`
    14     (b) n rows of which 50 repeat existing event_ids
    15     (c) n rows of which 20 have a negative `amount`
    16     (d) a half-size append of n // 2 clean rows

    New events get event_ids past the table's maximum and event_ts shifted by +30 days (past the
    original window). The duplicates of (b) stay in the table, so `unique.event_id` keeps failing
    on (c) and (d) as well: a table-level fault does not go away.
    """
    from lakehouse import contract, gen, tables
    from lakehouse.spec import ARROW_SCHEMA_V1

    table = contract.build_canonical_table(warehouse, rows=rows)
    tables.compact(table, "L1")
    n = max(rows // 10, 2)
    next_id = int(pc.max(contract.to_arrow(table)["event_id"]).as_py()) + 1
    shift = pa.scalar(dt.timedelta(days=30), type=pa.duration("us"))

    def fresh(i: int, size: int = n) -> pa.Table:
        nonlocal next_id
        b = gen.generate_events(size, seed=DEMO_SEED + i)
        b = b.set_column(0, "event_id", pa.array(np.arange(next_id, next_id + size, dtype="int64")))
        b = b.set_column(2, "event_ts", pc.add(b["event_ts"], shift))
        next_id += size
        return b

    def replace(b: pa.Table, name: str, values: np.ndarray, mask_rows: np.ndarray) -> pa.Table:
        col = b[name].to_numpy(zero_copy_only=False).copy()
        col[mask_rows] = values
        return b.set_column(b.schema.get_field_index(name), name, pa.array(col, from_pandas=True))

    def append(b: pa.Table) -> None:
        tables.append_batches(table, [b.cast(ARROW_SCHEMA_V1)], "L1")

    append(fresh(0))
    append(fresh(1))

    # (a) at least 10 purchases, 30% of them without an amount
    b = fresh(2)
    names = b["event_name"].to_pylist()
    amounts = b["amount"].to_numpy(zero_copy_only=False).astype("float64")
    buy = [i for i, name in enumerate(names) if name == "purchase"]
    for i in range(min(10, len(names))):
        if len(buy) >= 10:
            break
        if i not in buy:
            names[i] = "purchase"
            amounts[i] = 25.0
            buy.append(i)
    buy.sort()
    for i in buy[: int(np.ceil(DEMO_NULL_SHARE * len(buy)))]:
        amounts[i] = np.nan
    b = b.set_column(3, "event_name", pa.array(names, type=pa.string()))
    b = b.set_column(6, "amount", pa.array(amounts, from_pandas=True))
    append(b)

    # (b) repeated event_ids
    b = fresh(3)
    k_dup = min(DEMO_DUPLICATE_IDS, n // 2)
    b = replace(b, "event_id", np.arange(k_dup, dtype="int64"), np.arange(k_dup))
    append(b)

    # (c) negative amounts
    b = fresh(4)
    k_neg = min(DEMO_NEGATIVE_ROWS, n)
    b = replace(b, "amount", -(10.0 + np.arange(k_neg, dtype="float64")), np.arange(k_neg))
    append(b)

    # (d) half-size append
    append(fresh(5, max(n // 2, 1)))
    return table
