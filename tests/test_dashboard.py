"""The static dashboard: sections, escaping, missing summaries, shape of the output."""

from __future__ import annotations

import re

import numpy as np
import pandas as pd
import pytest

from dq import dashboard, store, summary
from dq.spec import ARMS, FAULT_TYPES, SEVERITIES

TITLES = list(dashboard.TITLES.values())


def _recall_rows() -> pd.DataFrame:
    rows = []
    for world in ("W1", "W0"):
        for arm in ARMS:
            for k in [np.nan] if arm == "S0" else [4.0, 8.0]:
                for fi, fault in enumerate(FAULT_TYPES):
                    for si, sev in enumerate(SEVERITIES):
                        d = (fi + si + (1 if arm == "A" else 0)) % 6
                        rows.append(
                            {
                                "world": world,
                                "arm": arm,
                                "k": k,
                                "fault": fault,
                                "severity": sev,
                                "detected": min(d, 5),
                                "n": 5,
                                "recall": min(d, 5) / 5,
                                "ci_low": 0.1,
                                "ci_high": 0.9,
                            }
                        )
    return pd.DataFrame(rows)


def _false_alarms() -> pd.DataFrame:
    rows = []
    for arm in ARMS:
        for k in [np.nan] if arm == "S0" else [4.0]:
            for check, family in (("volume.rows", "volume"), ("not_null.city_id", "nulls")):
                rows.append(
                    {
                        "world": "W1",
                        "arm": arm,
                        "k": k,
                        "check": check,
                        "family": family,
                        "alarms": 2,
                        "batch_checks": 230,
                        "per_100": 2 / 230 * 100,
                    }
                )
    return pd.DataFrame(rows)


def _ttd() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "world": "W1",
                "arm": "A",
                "k": 4.0,
                "fault": "volume",
                "severity": "small",
                "n_detected": 4,
                "ttd_median": 1.0,
                "ttd_max": 3.0,
            },
            {
                "world": "W1",
                "arm": "S1",
                "k": 4.0,
                "fault": "volume",
                "severity": "small",
                "n_detected": 0,
                "ttd_median": np.nan,
                "ttd_max": np.nan,
            },
        ]
    )


def _lake() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "walk": ["logical", "naive"],
            "version": [3, 3],
            "snapshot_id": [111, 112],
            "kind": ["maintenance", "append"],
            "check": ["volume.rows", "volume.rows"],
            "family": ["volume", "volume"],
            "status": ["pass", "fail"],
            "observed": [100.0, 0.0],
            "expected": ["", ""],
            "detail": ["", ""],
        }
    )


def _all_summaries(evil: str = "plain") -> dict[str, pd.DataFrame]:
    return {
        "recall": _recall_rows(),
        "false_alarms": _false_alarms(),
        "ttd": _ttd(),
        "operating_points": pd.DataFrame(
            {
                "world": ["W1", "W1"],
                "arm": ["S1", "A"],
                "k": [8.0, 4.0],
                "fa_per_100": [0.9, 0.8],
                "reached_target": [True, True],
            }
        ),
        "verdicts": pd.DataFrame(
            {
                "hypothesis": ["H1", "H2"],
                "world": ["W1", "W1"],
                "verdict": ["supported", f"not supported {evil}"],
                "detail": ["a=3 s=1", "n/a"],
            }
        ),
        "mismatches": pd.DataFrame(
            {
                "arm": ["A"],
                "fault": ["volume"],
                "severity": ["large"],
                "family": ["unique"],
                "predicted": ["yes"],
                "seeds_detected": [1],
                "n": [5],
            }
        ),
        "post_fault_alarms": pd.DataFrame(
            {
                "world": ["W1", "W1"],
                "arm": ["A", "S0"],
                "k": [4.0, np.nan],
                "fault": ["volume", "volume"],
                "severity": ["small", "small"],
                "alarms": [2, 0],
            }
        ),
        "lake": _lake(),
    }


def _stream_rows(n_batches: int = 70) -> pd.DataFrame:
    rows = []
    for b in range(1, n_batches + 1):
        for check, fam in (("volume.rows", "volume"), ("not_null.city_id", "nulls")):
            rows.append(
                {
                    "world": "W1",
                    "seed": 3601,
                    "stream": "fault:volume:small",
                    "batch": b,
                    "check_id": check,
                    "family": fam,
                    "mode": "rolling",
                    "k": 4.0,
                    "status": "fail" if (b == 20 and check == "volume.rows") else "pass",
                    "observed": float(b),
                }
            )
    return pd.DataFrame(rows)


@pytest.fixture
def full_store(tmp_path):
    path = tmp_path / "r.sqlite"
    run = store.write_run(
        path,
        "smoke <b>",
        "smoke",
        {"seeds": [3601], "worlds": ["W1"], "ks": [4.0, 8.0]},
        git_sha="abc1234",
    )
    for name, df in _all_summaries().items():
        store.write_summary(path, run, name, df)
    store.write_results(path, run, _stream_rows(), source="stream")
    return path, run


def _html(path):
    return path.read_text(encoding="utf-8")


def test_full_run_has_every_section(full_store, tmp_path):
    path, _ = full_store
    out = dashboard.build(path, tmp_path / "site")
    assert out == tmp_path / "site" / "index.html"
    text = _html(out)
    for title in TITLES:
        assert f"<h2>{title}</h2>" in text, title
    assert "not computed in this run" not in text
    assert "smoke &lt;b&gt;" in text and "abc1234" in text
    assert "3601" in text and "W1" in text
    assert text.count("<table") >= 8


def test_recall_heatmap_uses_k4_for_threshold_arms_and_none_for_s0(full_store, tmp_path):
    path, _ = full_store
    text = _html(dashboard.build(path, tmp_path / "site"))
    assert "S0</th>" in text
    assert "S1 (k = 4)" in text and "A (k = 4)" in text
    assert "S1 (k = 8)" in text  # operating-point table
    assert re.search(r'<td class="heat" style="--r:0\.600"[^>]*>3/5</td>', text)
    assert "recall 100%" in text


def test_false_alarms_and_ttd_and_other_tables(full_store, tmp_path):
    path, _ = full_store
    text = _html(dashboard.build(path, tmp_path / "site"))
    assert "0.87 <span" in text  # 2 / 230 per 100
    assert "1 (max 3, n = 4)" in text
    assert "seeds_detected" in text
    assert "snapshot_id" in text and "maintenance" in text
    assert "✗</span> fail" in text and "✓</span> pass" in text  # symbols, not colour alone
    assert re.search(r"<tr><td>W1</td><td>A</td>.*?<td class=\"num\">2</td></tr>", text, re.S)


def test_stream_grid_is_capped_and_marks_the_failure(full_store, tmp_path):
    path, _ = full_store
    text = _html(dashboard.build(path, tmp_path / "site"))
    grid = text[text.index('<table class="grid">') :]
    grid = grid[: grid.index("</table>")]
    header = grid[: grid.index("</thead>")]
    assert header.count("<th>") == 60
    assert "<th>70</th>" in header and "<th>11</th>" in header and "<th>10</th>" not in header
    assert grid.count("<tr>") == 1 + 2 * 1  # header + two check rows
    assert "volume.rows [rolling]" in grid
    assert 'title="volume.rows, batch 20: fail, observed 20"' in grid
    assert "st-fail" in grid


def test_values_are_escaped(tmp_path):
    path = tmp_path / "r.sqlite"
    run = store.write_run(path, "x", "smoke", {"note": "<script>alert(1)</script>"})
    evil = "<script>alert(1)</script>"
    for name, df in _all_summaries(evil).items():
        store.write_summary(path, run, name, df)
    rows = _stream_rows(3).assign(check_id="<script>alert(2)</script>")
    store.write_results(path, run, rows, source="stream")
    text = _html(dashboard.build(path, tmp_path / "site"))
    assert "<script" not in text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text
    assert "&lt;script&gt;alert(2)&lt;/script&gt;" in text


def test_store_without_summaries_says_not_computed(tmp_path):
    path = tmp_path / "r.sqlite"
    store.write_run(path, "empty run", "smoke", {})
    text = _html(dashboard.build(path, tmp_path / "site"))
    for title in TITLES:
        assert f"<h2>{title}</h2>" in text
    assert text.count("not computed in this run") == len(TITLES)
    assert "empty run" in text


def test_partial_run_only_marks_the_missing_sections(tmp_path):
    path = tmp_path / "r.sqlite"
    run = store.write_run(path, "partial", "smoke", {})
    store.write_summary(path, run, "verdicts", _all_summaries()["verdicts"])
    text = _html(dashboard.build(path, tmp_path / "site"))
    assert text.count("not computed in this run") == len(TITLES) - 1
    assert "supported" in text


def test_summary_with_no_rows_is_not_called_uncomputed(tmp_path):
    path = tmp_path / "r.sqlite"
    run = store.write_run(path, "r", "smoke", {})
    store.write_summary(path, run, "mismatches", summary.empty("mismatches"))
    text = _html(dashboard.build(path, tmp_path / "site"))
    section = text[text.index('<section id="mismatches">') :]
    section = section[: section.index("</section>")]
    assert "no rows" in section and "not computed" not in section


def test_empty_store_builds_a_placeholder_page(tmp_path):
    out = dashboard.build(tmp_path / "none.sqlite", tmp_path / "site")
    assert "no runs" in _html(out)


def test_run_id_selects_an_older_run(tmp_path):
    path = tmp_path / "r.sqlite"
    first = store.write_run(path, "first-run", "smoke", {})
    store.write_run(path, "second-run", "smoke", {})
    assert "second-run" in _html(dashboard.build(path, tmp_path / "a"))
    assert "first-run" in _html(dashboard.build(path, tmp_path / "b", run_id=first))
    with pytest.raises(ValueError, match="99"):
        dashboard.build(path, tmp_path / "c", run_id=99)


def test_page_is_self_contained_and_responsive(full_store, tmp_path):
    path, _ = full_store
    text = _html(dashboard.build(path, tmp_path / "site"))
    assert "<script" not in text
    assert not re.search(r"(?:src|href)=[\"']https?:", text)
    assert "@import" not in text and "url(" not in text
    assert 'name="viewport"' in text
    assert "prefers-color-scheme: dark" in text
    assert ":root {" in text and "--bg:" in text
    assert text.count('class="scroll"') >= 8  # tables scroll inside their own box
    assert "overflow-x: auto" in text
