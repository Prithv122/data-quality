"""A static results page built from the store: one self-contained `index.html`.

No script, no web fonts, no external request. Every value that reaches the page goes through
`html.escape`. A summary the run did not write shows "not computed in this run" instead of
failing the build.
"""

from __future__ import annotations

import html
import math
from pathlib import Path

import numpy as np
import pandas as pd

from dq import store
from dq.spec import ARMS, FAULT_TYPES, K_DEFAULT, SEVERITIES
from dq.types import FAMILIES

TITLES = {
    "verdicts": "Verdicts",
    "recall": "Recall",
    "recall_op": "Recall at the operating point",
    "false_alarms": "False alarms",
    "ttd": "Time to detect",
    "mismatches": "Mismatches",
    "post_fault": "Post-fault alarms",
    "lake": "Lake versions",
    "stream": "Latest stream",
}
DASH = chr(0x2013)
NOT_COMPUTED = "not computed in this run"
GRID_MAX_COLUMNS = 60

# status -> (symbol, label); the symbol and the word carry the meaning, colour only adds to it
STATUS_MARK = {
    "pass": ("✓", "pass"),
    "warn": ("▲", "warn"),
    "fail": ("✗", "fail"),
    "error": ("■", "error"),
    "skipped": (DASH, "skipped"),
}

_CSS = """
:root {
  color-scheme: light dark;
  --bg: #f6f7f9; --surface: #ffffff; --fg: #1b1f24; --muted: #565f6c; --border: #d5dae1;
  --heat: #5b9bd5; --accent: #1f5fa8;
  --pass-fg: #14692f; --pass-bg: #dcf3e1;
  --warn-fg: #7a4f00; --warn-bg: #fff0c7;
  --fail-fg: #a5200f; --fail-bg: #fde1dd;
  --error-fg: #5f2a99; --error-bg: #eadcf8;
  --skipped-fg: #565f6c; --skipped-bg: #e9ecf0;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #12151a; --surface: #1a1f26; --fg: #e6e9ee; --muted: #9aa4b2; --border: #2e3641;
    --heat: #2c5f94; --accent: #7ab0ea;
    --pass-fg: #7ee09a; --pass-bg: #163b22;
    --warn-fg: #f2c566; --warn-bg: #413012;
    --fail-fg: #ff9d90; --fail-bg: #4a1d19;
    --error-fg: #cfa9f5; --error-bg: #34224d;
    --skipped-fg: #9aa4b2; --skipped-bg: #272d36;
  }
}
:root[data-theme="dark"] {
  --bg: #12151a; --surface: #1a1f26; --fg: #e6e9ee; --muted: #9aa4b2; --border: #2e3641;
  --heat: #2c5f94; --accent: #7ab0ea;
  --pass-fg: #7ee09a; --pass-bg: #163b22;
  --warn-fg: #f2c566; --warn-bg: #413012;
  --fail-fg: #ff9d90; --fail-bg: #4a1d19;
  --error-fg: #cfa9f5; --error-bg: #34224d;
  --skipped-fg: #9aa4b2; --skipped-bg: #272d36;
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body {
  margin: 0; background: var(--bg); color: var(--fg);
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}
main { max-width: 1100px; margin: 0 auto; padding: 24px 16px 48px; min-width: 0; }
h1 { font-size: 1.5rem; margin: 0 0 4px; }
h2 { font-size: 1.15rem; margin: 0 0 8px; }
h3 { font-size: 1rem; margin: 18px 0 6px; color: var(--muted); font-weight: 600; }
section {
  background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
  padding: 16px; margin: 16px 0; min-width: 0;
}
.sub, .note, .empty { color: var(--muted); font-size: .9rem; margin: 4px 0 8px; }
.empty { font-style: italic; }
dl.meta { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 8px 16px;
  margin: 12px 0 0; }
dl.meta dt {
  color: var(--muted); font-size: .8rem; text-transform: uppercase; letter-spacing: .04em;
}
dl.meta dd { margin: 0; overflow-wrap: anywhere; }
nav { display: flex; flex-wrap: wrap; gap: 6px 14px; margin: 12px 0 0; font-size: .9rem; }
nav a { color: var(--accent); }
.scroll { overflow-x: auto; max-width: 100%; border: 1px solid var(--border); border-radius: 6px; }
table { border-collapse: collapse; width: 100%; font-size: .9rem; }
th, td {
  padding: 5px 10px; text-align: left; white-space: nowrap; border-bottom: 1px solid var(--border);
}
tr:last-child td { border-bottom: 0; }
th { background: var(--bg); font-weight: 600; }
td.wrap { white-space: normal; min-width: 14rem; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
td.heat {
  text-align: center; font-variant-numeric: tabular-nums; color: var(--fg);
  background: var(--surface);
  background: color-mix(in srgb, var(--heat) calc(var(--r, 0) * 100%), var(--surface));
}
.st { display: inline-block; padding: 0 7px; border-radius: 10px; font-size: .82rem; }
.st-pass { color: var(--pass-fg); background: var(--pass-bg); }
.st-warn { color: var(--warn-fg); background: var(--warn-bg); }
.st-fail { color: var(--fail-fg); background: var(--fail-bg); }
.st-error { color: var(--error-fg); background: var(--error-bg); }
.st-skipped { color: var(--skipped-fg); background: var(--skipped-bg); }
table.grid td, table.grid th {
  padding: 2px 0; min-width: 1.5rem; text-align: center; font-size: .75rem;
}
table.grid td.st { border-radius: 0; display: table-cell; }
table.grid th.rowlabel, table.grid td.rowlabel {
  text-align: left; padding: 2px 10px; position: sticky; left: 0; background: var(--surface);
  font-size: .85rem; border-right: 1px solid var(--border);
}
.sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
.legend { display: flex; flex-wrap: wrap; gap: 6px 12px; margin: 8px 0; font-size: .85rem; }
details { margin-top: 12px; }
summary { cursor: pointer; color: var(--accent); }
footer { color: var(--muted); font-size: .8rem; margin-top: 24px; }
"""


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


def _isnull(x: object) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x)) or x is pd.NA


def _num(x: object, digits: int | None = None) -> str:
    if _isnull(x):
        return DASH
    if isinstance(x, (int, np.integer)):
        return str(int(x))
    if isinstance(x, (float, np.floating)):
        if digits is not None:
            return f"{float(x):.{digits}f}"
        return str(int(x)) if float(x).is_integer() else f"{float(x):.3g}"
    return str(x)


def _text(x: object) -> str:
    return DASH if _isnull(x) else str(x)


def _scroll(inner: str) -> str:
    return f'<div class="scroll" tabindex="0">{inner}</div>'


def _status_chip(status: object) -> str:
    key = str(status)
    symbol, label = STATUS_MARK.get(key, ("?", key))
    cls = key if key in STATUS_MARK else "skipped"
    return (
        f'<span class="st st-{_e(cls)}"><span aria-hidden="true">{symbol}</span> {_e(label)}</span>'
    )


def _table(df: pd.DataFrame, numeric: set[str] | None = None, wrap: set[str] | None = None) -> str:
    numeric = numeric or set()
    wrap = wrap or set()
    head = "".join(
        f'<th class="num">{_e(c)}</th>' if c in numeric else f"<th>{_e(c)}</th>" for c in df.columns
    )
    rows = []
    for rec in df.itertuples(index=False, name=None):
        cells = []
        for col, value in zip(df.columns, rec, strict=True):
            if col == "status":
                cells.append(f"<td>{_status_chip(value)}</td>")
            elif col in numeric:
                cells.append(f'<td class="num">{_e(_num(value))}</td>')
            else:
                cls = ' class="wrap"' if col in wrap else ""
                cells.append(f"<td{cls}>{_e(_text(value))}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return _scroll(f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>")


def _section(key: str, body: str | None, note: str = "") -> str:
    title = TITLES[key]
    content = body if body is not None else f'<p class="empty">{_e(NOT_COMPUTED)}</p>'
    sub = f'<p class="sub">{_e(note)}</p>' if note and body is not None else ""
    return f'<section id="{_e(key)}"><h2>{_e(title)}</h2>{sub}{content}</section>'


def _worlds(df: pd.DataFrame) -> list[str]:
    seen = list(dict.fromkeys(df["world"].astype(str)))
    order = {"W1": 0, "W0": 1}
    return sorted(seen, key=lambda w: (order.get(w, 9), w))


def _ordered(values: set[str], canon: tuple[str, ...]) -> list[str]:
    return [v for v in canon if v in values] + sorted(v for v in values if v not in canon)


def _arm_columns(df: pd.DataFrame) -> list[str]:
    return _ordered(set(df["arm"].astype(str)), ARMS)


def _at_k(df: pd.DataFrame, ks: dict[str, float | None]) -> pd.DataFrame:
    """Rows at each arm's chosen k; an arm without a k (S0) uses its NaN-k rows."""
    keep = pd.Series(False, index=df.index)
    for arm, k in ks.items():
        is_arm = df["arm"].astype(str) == arm
        if k is None or _isnull(k):
            keep |= is_arm & df["k"].isna()
        else:
            keep |= is_arm & np.isclose(df["k"].astype(float), float(k), equal_nan=False)
    return df[keep]


def _default_ks(df: pd.DataFrame) -> dict[str, float | None]:
    """k = 4 for the threshold arms, none for S0."""
    return {a: (None if a == "S0" else K_DEFAULT) for a in _arm_columns(df)}


def _op_ks(df: pd.DataFrame, op: pd.DataFrame, world: str) -> dict[str, float | None]:
    ks = _default_ks(df)
    sub = op[op["world"].astype(str) == world]
    for arm in ks:
        if arm == "S0":
            continue
        hit = sub[sub["arm"].astype(str) == arm]
        if not hit.empty and not _isnull(hit.iloc[0]["k"]):
            ks[arm] = float(hit.iloc[0]["k"])
    return ks


def _cells_order(df: pd.DataFrame) -> list[tuple[str, str]]:
    faults = _ordered(set(df["fault"].astype(str)), FAULT_TYPES)
    sevs = set(df["severity"].astype(str))
    out = []
    for f in faults:
        present = set(df.loc[df["fault"].astype(str) == f, "severity"].astype(str))
        out.extend((f, s) for s in _ordered(present & sevs, SEVERITIES))
    return out


def _arm_header(arm: str, ks: dict[str, float | None]) -> str:
    k = ks.get(arm)
    return f"{arm}" if k is None else f"{arm} (k = {k:g})"


def _heat_td(detected: object, n: object) -> str:
    if _isnull(detected) or _isnull(n) or float(n) == 0:
        return f'<td class="heat">{DASH}</td>'
    d, total = round(float(detected)), round(float(n))
    r = max(0.0, min(1.0, d / total))
    return f'<td class="heat" style="--r:{r:.3f}" title="recall {r:.0%}">{d}/{total}</td>'


def _recall_world(recall: pd.DataFrame, ks: dict[str, float | None]) -> str:
    sel = _at_k(recall, ks)
    arms = [a for a in ks if a in set(sel["arm"].astype(str))]
    lookup = {
        (str(r.fault), str(r.severity), str(r.arm)): (r.detected, r.n)
        for r in sel.itertuples(index=False)
    }
    head = "<th>Fault</th><th>Severity</th>" + "".join(
        f"<th>{_e(_arm_header(a, ks))}</th>" for a in arms
    )
    rows = []
    for fault, sev in _cells_order(sel):
        cells = "".join(_heat_td(*lookup.get((fault, sev, a), (None, None))) for a in arms)
        rows.append(f"<tr><td>{_e(fault)}</td><td>{_e(sev)}</td>{cells}</tr>")
    if not rows:
        return '<p class="empty">no rows at this k</p>'
    return _scroll(f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>")


def _recall_section(recall: pd.DataFrame | None, op: pd.DataFrame | None, at_op: bool) -> str:
    if recall is None or (at_op and op is None):
        return _section("recall_op" if at_op else "recall", None)
    key = "recall_op" if at_op else "recall"
    if recall.empty:
        return _section(key, '<p class="empty">no rows</p>')
    parts = []
    for world in _worlds(recall):
        sub = recall[recall["world"].astype(str) == world]
        ks = _op_ks(sub, op, world) if at_op and op is not None else _default_ks(sub)
        parts.append(f"<h3>World {_e(world)}</h3>" + _recall_world(sub, ks))
    note = (
        "Cells are detected / seeds. Each arm at the smallest k whose clean-stream false-alarm "
        "rate meets the target."
        if at_op
        else "Cells are detected / seeds, shaded by recall. S1 and A at k = 4; S0 has no k."
    )
    return _section(key, "".join(parts), note)


def _false_alarm_section(fa: pd.DataFrame | None) -> str:
    if fa is None:
        return _section("false_alarms", None)
    if fa.empty:
        return _section("false_alarms", '<p class="empty">no rows</p>')
    parts = []
    for world in _worlds(fa):
        sub = fa[fa["world"].astype(str) == world]
        sel = _at_k(sub, _default_ks(sub))
        arms = [a for a in _arm_columns(sub) if a in set(sel["arm"].astype(str))]
        fams = _ordered(set(sel["family"].astype(str)), FAMILIES)
        grouped = sel.groupby([sel["arm"].astype(str), sel["family"].astype(str)])[
            ["alarms", "batch_checks"]
        ].sum()
        head = (
            "<th>Arm</th>"
            + "".join(f'<th class="num">{_e(f)}</th>' for f in fams)
            + '<th class="num">all</th>'
        )
        rows = []
        for arm in arms:
            cells = []
            total_a = total_b = 0.0
            for fam in fams:
                if (arm, fam) in grouped.index:
                    a, b = grouped.loc[(arm, fam)]
                    total_a += a
                    total_b += b
                    cells.append(_fa_cell(a, b))
                else:
                    cells.append(f'<td class="num">{DASH}</td>')
            cells.append(_fa_cell(total_a, total_b))
            rows.append(
                f"<tr><td>{_e(_arm_header(arm, _default_ks(sub)))}</td>{''.join(cells)}</tr>"
            )
        parts.append(
            f"<h3>World {_e(world)}</h3>"
            + _scroll(
                f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>"
            )
            + _fa_by_check(sel)
        )
    note = (
        "Alarms per 100 batch-checks on clean streams, scored batches only (alarms / batch-checks)."
    )
    return _section("false_alarms", "".join(parts), note)


def _fa_cell(alarms: float, batch_checks: float) -> str:
    if not batch_checks:
        return f'<td class="num">{DASH}</td>'
    per = 100.0 * alarms / batch_checks
    counts = f"({_num(alarms)}/{_num(batch_checks)})"
    return f'<td class="num">{per:.2f} <span class="sub">{counts}</span></td>'


def _fa_by_check(sel: pd.DataFrame) -> str:
    arms = _arm_columns(sel)
    checks = list(dict.fromkeys(sel["check"].astype(str)))
    lookup = {(str(r.check), str(r.arm)): r.per_100 for r in sel.itertuples(index=False)}
    head = "<th>Check</th>" + "".join(f'<th class="num">{_e(a)}</th>' for a in arms)
    rows = []
    for c in checks:
        cells = "".join(f'<td class="num">{_e(_num(lookup.get((c, a)), 2))}</td>' for a in arms)
        rows.append(f"<tr><td>{_e(c)}</td>{cells}</tr>")
    table = _scroll(f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>")
    return f"<details><summary>By check</summary>{table}</details>"


def _ttd_section(ttd: pd.DataFrame | None) -> str:
    if ttd is None:
        return _section("ttd", None)
    if ttd.empty:
        return _section("ttd", '<p class="empty">no rows</p>')
    parts = []
    for world in _worlds(ttd):
        sub = ttd[ttd["world"].astype(str) == world]
        ks = _default_ks(sub)
        sel = _at_k(sub, ks)
        arms = [a for a in ks if a in set(sel["arm"].astype(str))]
        lookup = {
            (str(r.fault), str(r.severity), str(r.arm)): r for r in sel.itertuples(index=False)
        }
        head = "<th>Fault</th><th>Severity</th>" + "".join(
            f"<th>{_e(_arm_header(a, ks))}</th>" for a in arms
        )
        rows = []
        for fault, sev in _cells_order(sel):
            cells = []
            for arm in arms:
                r = lookup.get((fault, sev, arm))
                if r is None or _isnull(r.n_detected) or float(r.n_detected) == 0:
                    cells.append(f"<td>{DASH}</td>")
                else:
                    cells.append(
                        f"<td>{_e(_num(r.ttd_median))} (max {_e(_num(r.ttd_max))}, "
                        f"n = {_e(_num(r.n_detected))})</td>"
                    )
            rows.append(f"<tr><td>{_e(fault)}</td><td>{_e(sev)}</td>{''.join(cells)}</tr>")
        parts.append(
            f"<h3>World {_e(world)}</h3>"
            + _scroll(
                f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>"
            )
        )
    note = "Median batches from onset to the first attributable alarm; a dash: no seed detected it."
    return _section("ttd", "".join(parts), note)


def _simple_section(key: str, df: pd.DataFrame | None, numeric=(), wrap=(), note: str = "") -> str:
    if df is None:
        return _section(key, None)
    if df.empty:
        return _section(key, '<p class="empty">no rows</p>')
    return _section(key, _table(df, set(numeric), set(wrap)), note)


def _post_fault_section(pf: pd.DataFrame | None) -> str:
    if pf is None:
        return _section("post_fault", None)
    sel = _at_k(pf, _default_ks(pf)) if not pf.empty else pf
    sel = sel[sel["alarms"].fillna(0) > 0] if not sel.empty else sel
    if sel.empty:
        return _section("post_fault", '<p class="empty">none at k = 4 (S0 has no k)</p>')
    shown = sel.drop(columns=["k"])
    return _section(
        "post_fault",
        _table(shown, numeric={"alarms"}),
        "Alarms after a fault window closes that the clean twin does not have.",
    )


def _lake_section(lake: pd.DataFrame | None) -> str:
    if lake is None:
        return _section("lake", None)
    if lake.empty:
        return _section("lake", '<p class="empty">no rows</p>')
    shown = lake[["walk", "version", "snapshot_id", "kind", "check", "status"]]
    return _section("lake", _table(shown, numeric={"version", "snapshot_id"}))


def _grid_section(rows: pd.DataFrame | None) -> str:
    if rows is None or rows.empty:
        return _section("stream", None)
    df = rows.copy()
    if df["k"].notna().any():
        ks = df["k"].dropna().astype(float)
        target = K_DEFAULT if np.isclose(ks, K_DEFAULT).any() else float(ks.min())
        df = df[df["k"].isna() | np.isclose(df["k"].astype(float).fillna(0.0), target)]
    by_batch = df["batch"].notna().any()
    axis = "batch" if by_batch else "snapshot"
    key = df["batch"] if by_batch else df["snapshot_id"]
    df = df.assign(colkey=key)
    df = df[df["colkey"].notna()]
    if df.empty:
        return _section("stream", None)
    cols = sorted(set(df["colkey"].astype(int)))[-GRID_MAX_COLUMNS:]
    df = df[df["colkey"].astype(int).isin(cols)]
    mode = df["mode"].astype(object).where(df["mode"].notna(), "")
    df = df.assign(
        rowkey=[f"{c} [{m}]" if m else str(c) for c, m in zip(df["check_id"], mode, strict=True)]
    )
    labels = list(dict.fromkeys(df["rowkey"]))
    status = {}
    detail = {}
    for r in df.itertuples(index=False):
        k = (r.rowkey, int(r.colkey))
        if k not in status:
            status[k] = str(r.status)
            obs = "" if _isnull(r.observed) else f", observed {_num(r.observed)}"
            detail[k] = f"{r.check_id}, {axis} {int(r.colkey)}: {r.status}{obs}"
    head = '<th class="rowlabel">check</th>' + "".join(f"<th>{c}</th>" for c in cols)
    body = []
    for label in labels:
        cells = []
        for c in cols:
            s = status.get((label, c))
            if s is None:
                cells.append("<td></td>")
                continue
            symbol, word = STATUS_MARK.get(s, ("?", s))
            cls = s if s in STATUS_MARK else "skipped"
            cells.append(
                f'<td class="st st-{_e(cls)}" title="{_e(detail[(label, c)])}">'
                f'<span aria-hidden="true">{symbol}</span><span class="sr">{_e(word)}</span></td>'
            )
        body.append(f'<tr><td class="rowlabel">{_e(label)}</td>{"".join(cells)}</tr>')
    first = df.iloc[-1]
    origin = " / ".join(
        _e(first[c]) for c in ("source", "world", "seed", "stream") if not _isnull(first[c])
    )
    legend = "".join(
        f'<span class="st st-{k}"><span aria-hidden="true">{s}</span> {_e(w)}</span>'
        for k, (s, w) in STATUS_MARK.items()
    )
    note = f"{origin}. Columns are {axis}s" + (
        f", last {GRID_MAX_COLUMNS} shown." if len(cols) == GRID_MAX_COLUMNS else "."
    )
    table = (
        f'<table class="grid"><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table>'
    )
    return _section("stream", f'<div class="legend">{legend}</div>{_scroll(table)}', note)


def _meta_block(run: pd.Series, meta: dict) -> str:
    items: list[tuple[str, str]] = [
        ("Run", f"#{int(run['run_id'])}"),
        ("Scale", _text(run["scale"])),
        ("Created", _text(run["created_at"])),
        ("Git", _text(run["git_sha"])),
    ]
    for key in ("worlds", "seeds", "ks"):
        if key in meta:
            value = meta[key]
            if isinstance(value, (list, tuple)):
                value = ", ".join(f"{v:g}" if isinstance(v, float) else str(v) for v in value)
            items.append((key, str(value)))
    for key in sorted(meta):
        if key not in ("worlds", "seeds", "ks"):
            items.append((key, str(meta[key])))
    cells = "".join(f"<div><dt>{_e(k)}</dt><dd>{_e(v)}</dd></div>" for k, v in items)
    return f'<dl class="meta">{cells}</dl>'


def _read(path: Path, run_id: int, names: set[str], name: str) -> pd.DataFrame | None:
    return store.read_summary(path, run_id, name) if name in names else None


def _page(title: str, header: str, nav: str, sections: list[str]) -> str:
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{_e(title)}</title>\n<style>{_CSS}</style>\n</head>\n<body>\n<main>\n"
        f"<header>{header}{nav}</header>\n{''.join(sections)}\n"
        "<footer>Static page built from the results store.</footer>\n"
        "</main>\n</body>\n</html>\n"
    )


def build(store_path: str | Path, out_dir: str | Path, run_id: int | None = None) -> Path:
    """Write `index.html` for one run (the latest by default) and return its path."""
    store_path = Path(store_path)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    target = out / "index.html"
    chosen = store.latest_run_id(store_path) if run_id is None else run_id
    nav = "<nav>" + "".join(f'<a href="#{_e(k)}">{_e(t)}</a>' for k, t in TITLES.items()) + "</nav>"
    if chosen is None:
        header = '<h1>Data quality results</h1><p class="sub">The store holds no runs yet.</p>'
        page = _page("Data quality results", header, "", [])
        target.write_text(page, encoding="utf-8", newline="\n")
        return target
    runs = store.read_runs(store_path)
    hit = runs[runs["run_id"] == chosen]
    if hit.empty:
        raise ValueError(f"run {chosen} is not in {store_path}")
    run = hit.iloc[0]
    meta = store.read_meta(store_path, chosen)
    names = set(store.summary_names(store_path, chosen))

    def get(name: str) -> pd.DataFrame | None:
        return _read(store_path, chosen, names, name)

    recall, op = get("recall"), get("operating_points")
    stream_rows = store.read_latest_stream(store_path, chosen)
    sections = [
        _simple_section("verdicts", get("verdicts"), wrap={"detail"}),
        _recall_section(recall, None, at_op=False),
        _recall_section(recall, op, at_op=True),
        _false_alarm_section(get("false_alarms")),
        _ttd_section(get("ttd")),
        _simple_section(
            "mismatches",
            get("mismatches"),
            numeric={"seeds_detected", "n"},
            note="Predicted versus observed families at k = 4, world W1; only cells that differ.",
        ),
        _post_fault_section(get("post_fault_alarms")),
        _lake_section(get("lake")),
        _grid_section(None if stream_rows.empty else stream_rows),
    ]
    label = _text(run["label"])
    header = f"<h1>Data quality results: {_e(label)}</h1>" + _meta_block(run, meta)
    page = _page(f"Data quality results: {label}", header, nav, sections)
    target.write_text(page, encoding="utf-8", newline="\n")
    return target
