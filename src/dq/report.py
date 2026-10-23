"""Render the evaluation tables as REPORT.md and one CSV per table. Numbers come from the tables."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from pathlib import Path

import pandas as pd

from dq import spec, summary
from dq.types import FAMILIES

DASH = chr(0x2013)  # en dash, used in intervals and ranges


def _table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> list[str]:
    def esc(x: object) -> str:
        return str(x).replace("|", "\\|")

    out = ["| " + " | ".join(esc(h) for h in headers) + " |"]
    out.append("|" + "|".join("---" for _ in headers) + "|")
    out.extend("| " + " | ".join(esc(c) for c in row) + " |" for row in rows)
    return [*out, ""]


def _k_key(k: float) -> float | None:
    return None if k is None or (isinstance(k, float) and math.isnan(k)) else round(float(k), 6)


def _fmt_k(k: float | None) -> str:
    return "" if k is None else f"{k:g}"


def _meta_text(value: object) -> str:
    if isinstance(value, (list, tuple)):
        return ", ".join(f"{v:g}" if isinstance(v, float) else str(v) for v in value)
    return str(value)


def _get(tables: Mapping[str, pd.DataFrame], name: str) -> pd.DataFrame:
    df = tables.get(name)
    return summary.empty(name) if df is None else df


def _worlds(tables: Mapping[str, pd.DataFrame]) -> list[str]:
    seen = set(_get(tables, "recall")["world"]) | set(_get(tables, "false_alarms")["world"])
    return [w for w in spec.WORLDS if w in seen]


def _columns(
    tables: Mapping[str, pd.DataFrame], world: str, k_default: float
) -> list[tuple[str, str, float | None]]:
    """(header, arm, k) for the arms shown side by side: S0, then S1 and A at the default k
    and at their operating points."""
    ops = _get(tables, "operating_points")
    ops = ops.loc[ops["world"] == world].set_index("arm") if len(ops) else ops
    cols: list[tuple[str, str, float | None]] = [("S0", "S0", None)]
    cols += [(f"{arm} k={k_default:g}", arm, k_default) for arm in ("S1", "A")]
    for arm in ("S1", "A"):
        if len(ops) and arm in ops.index:
            k = float(ops.loc[arm, "k"])
            cols.append((f"{arm} at operating point k={k:g}", arm, k))
    return cols


def _recall_cell(index: dict, key: tuple) -> str:
    row = index.get(key)
    if row is None:
        return "n/a"
    return f"{int(row.detected)}/{int(row.n)} ({row.ci_low:.2f}{DASH}{row.ci_high:.2f})"


def _recall_section(tables, world: str, k_default: float) -> list[str]:
    recall = _get(tables, "recall")
    recall = recall.loc[recall["world"] == world]
    index = {(r.arm, _k_key(r.k), r.fault, r.severity): r for r in recall.itertuples(index=False)}
    cols = _columns(tables, world, k_default)
    rows = []
    for fault in spec.FAULT_TYPES:
        for severity in spec.SEVERITIES:
            if not any(key[2:] == (fault, severity) for key in index):
                continue
            cells = [_recall_cell(index, (arm, _k_key(k), fault, severity)) for _, arm, k in cols]
            rows.append([fault, severity, *cells])
    out = [f"### Recall, world {world}", ""]
    out.append(
        "Detected conditions / conditions, Wilson 95% interval. Operating point: the smallest k "
        "whose clean false-alarm rate over the volume and null-rate checks meets the target."
    )
    out.append("")
    return out + _table(["Fault", "Severity", *[c[0] for c in cols]], rows)


def _fa_section(tables, world: str, k_default: float) -> list[str]:
    fa = _get(tables, "false_alarms")
    fa = fa.loc[fa["world"] == world]
    cols = _columns(tables, world, k_default)
    rows = []
    for header, arm, k in cols:
        sub = fa.loc[fa["arm"] == arm]
        sub = sub.loc[sub["k"].isna()] if k is None else sub.loc[(sub["k"] - k).abs() < 1e-9]
        if sub.empty:
            continue
        cells = []
        for fam in FAMILIES:
            part = sub.loc[sub["family"] == fam]
            alarms, total = int(part["alarms"].sum()), int(part["batch_checks"].sum())
            cells.append(f"{alarms}/{total} ({100.0 * alarms / total:.2f})" if total else "n/a")
        alarms, total = int(sub["alarms"].sum()), int(sub["batch_checks"].sum())
        cells.append(f"{alarms}/{total} ({100.0 * alarms / total:.2f})" if total else "n/a")
        rows.append([header, *cells])
    out = [f"### False alarms on clean streams, world {world}", ""]
    out.append(
        "Alarms / batch-checks (alarms per 100 batch-checks), scored batches "
        f"{spec.EVAL_FIRST}{DASH}{spec.N_BATCHES}."
    )
    out.append("")
    return out + _table(["Arm", *FAMILIES, "all"], rows)


def _ttd_section(tables, world: str, k_default: float) -> list[str]:
    ttd = _get(tables, "ttd")
    ttd = ttd.loc[ttd["world"] == world]
    index = {(r.arm, _k_key(r.k), r.fault, r.severity): r for r in ttd.itertuples(index=False)}
    cols = _columns(tables, world, k_default)
    rows = []
    for fault in spec.FAULT_TYPES:
        for severity in spec.SEVERITIES:
            if not any(key[2:] == (fault, severity) for key in index):
                continue
            cells = []
            for _, arm, k in cols:
                r = index.get((arm, _k_key(k), fault, severity))
                if r is None or r.n_detected == 0:
                    cells.append(DASH)
                else:
                    cells.append(f"{r.ttd_median:g} (max {r.ttd_max:g}), n={int(r.n_detected)}")
            rows.append([fault, severity, *cells])
    out = [f"### Time to detect, world {world}", ""]
    out.append("Batches from onset to the first attributable alarm: median (max), detected n.")
    out.append("")
    return out + _table(["Fault", "Severity", *[c[0] for c in cols]], rows)


def render_markdown(tables: Mapping[str, pd.DataFrame], meta: Mapping[str, object]) -> str:
    k_default = float(meta.get("k_default", spec.K_DEFAULT))  # type: ignore[arg-type]
    lines = ["# Data quality checks: what they catch", ""]

    lines += ["## Run", ""]
    for key in ("seeds", "worlds", "ks", "git_sha"):
        if meta.get(key) not in (None, "", [], ()):
            lines.append(f"- **{key}**: {_meta_text(meta[key])}")
    for key, value in meta.items():
        if key not in ("seeds", "worlds", "ks", "git_sha", "k_default"):
            lines.append(f"- **{key}**: {_meta_text(value)}")
    lines.append(
        f"- **stream**: {spec.N_BATCHES} batches, warm-up {spec.WARMUP_BATCHES}, "
        f"fault window {spec.FAULT_WINDOW} batches, default k {k_default:g}"
    )
    lines.append("")

    lines += ["## Verdicts", ""]
    verdicts = _get(tables, "verdicts")
    if verdicts.empty:
        lines += ["No verdicts computed.", ""]
    for r in verdicts.itertuples(index=False):
        lines.append(f"- **{r.hypothesis}**, world {r.world}: **{r.verdict}**. {r.detail}")
    if not verdicts.empty:
        lines.append("")

    ops = _get(tables, "operating_points")
    if not ops.empty:
        lines += ["### Operating points", ""]
        rows = [
            [r.world, r.arm, f"{r.k:g}", f"{r.fa_per_100:.2f}", "yes" if r.reached_target else "no"]
            for r in ops.itertuples(index=False)
        ]
        lines += _table(["World", "Arm", "k", "Clean FA per 100", "Target reached"], rows)

    lines += ["## Recall", ""]
    for world in _worlds(tables):
        lines += _recall_section(tables, world, k_default)

    lines += ["## False alarms", ""]
    for world in _worlds(tables):
        lines += _fa_section(tables, world, k_default)

    lines += ["## Time to detect", ""]
    for world in _worlds(tables):
        lines += _ttd_section(tables, world, k_default)

    lines += ["## Predicted versus observed families (world W1)", ""]
    mism = _get(tables, "mismatches")
    if mism.empty:
        lines += ["Every cell matches the pre-registered prediction.", ""]
    else:
        lines.append(f"{len(mism)} (arm, fault, severity, family) cells differ.")
        lines.append("")
        rows = [
            [
                r.arm,
                r.fault,
                r.severity,
                r.family,
                "detects" if r.predicted else "silent",
                f"{int(r.seeds_detected)}/{int(r.n)}",
            ]
            for r in mism.itertuples(index=False)
        ]
        lines += _table(["Arm", "Fault", "Severity", "Family", "Predicted", "Seeds detected"], rows)

    lines += ["## Alarms after the fault window", ""]
    post = _get(tables, "post_fault_alarms")
    shown = []
    for world in _worlds(tables):
        for _header, arm, k in _columns(tables, world, k_default):
            sub = post.loc[(post["world"] == world) & (post["arm"] == arm)]
            sub = sub.loc[sub["k"].isna()] if k is None else sub.loc[(sub["k"] - k).abs() < 1e-9]
            shown += [
                [r.world, r.arm, _fmt_k(_k_key(r.k)), r.fault, r.severity, int(r.alarms)]
                for r in sub.itertuples(index=False)
                if r.alarms > 0
            ]
    if shown:
        lines.append(
            "Alarms in a faulted stream after its window closed that the clean twin lacks."
        )
        lines.append("")
        lines += _table(["World", "Arm", "k", "Fault", "Severity", "Alarms"], shown)
    else:
        lines += ["None.", ""]
    return "\n".join(lines).rstrip() + "\n"


def write_results(
    tables: Mapping[str, pd.DataFrame], meta: Mapping[str, object], out_dir: str | Path
) -> Path:
    """Write REPORT.md and one `<table>.csv` per summary table; return the report path."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        df.to_csv(out / f"{name}.csv", index=False)
    report = out / "REPORT.md"
    report.write_text(render_markdown(tables, meta), encoding="utf-8", newline="\n")
    return report
