import pandas as pd

from dq import evaluate, summary
from dq.report import DASH, render_markdown, write_results


def _tables():
    lo, hi = evaluate.wilson(3, 5)
    recall = pd.DataFrame(
        [
            ("W1", "S0", float("nan"), "volume", "small", 0, 5, 0.0, 0.0, 0.43),
            ("W1", "S1", 4.0, "volume", "small", 3, 5, 0.6, lo, hi),
            ("W1", "A", 4.0, "volume", "small", 5, 5, 1.0, 0.57, 1.0),
            ("W1", "S1", 5.0, "volume", "small", 2, 5, 0.4, 0.12, 0.77),
            ("W1", "A", 6.0, "volume", "small", 4, 5, 0.8, 0.38, 0.96),
        ],
        columns=summary.SUMMARY_COLUMNS["recall"],
    )
    false_alarms = pd.DataFrame(
        [
            ("W1", "S0", float("nan"), "unique.city_id_date", "unique", 0, 230, 0.0),
            ("W1", "S1", 4.0, "volume.rows", "volume", 3, 230, 100 * 3 / 230),
            ("W1", "A", 4.0, "volume.rows", "volume", 1, 230, 100 * 1 / 230),
            ("W1", "S1", 5.0, "volume.rows", "volume", 0, 230, 0.0),
            ("W1", "A", 6.0, "volume.rows", "volume", 0, 230, 0.0),
        ],
        columns=summary.SUMMARY_COLUMNS["false_alarms"],
    )
    ttd = pd.DataFrame(
        [("W1", "A", 4.0, "volume", "small", 5, 1.0, 3.0)],
        columns=summary.SUMMARY_COLUMNS["ttd"],
    )
    ops = pd.DataFrame(
        [("W1", "S1", 5.0, 0.0, True), ("W1", "A", 6.0, 0.0, False)],
        columns=summary.SUMMARY_COLUMNS["operating_points"],
    )
    verdicts = pd.DataFrame(
        [
            ("H1", "W1", "supported", "a = 3, s = 0 over 10 conditions"),
            ("H2", "W1", "not supported", "A 1/15 at k = 4; fired: null_rate.precip_mm"),
        ],
        columns=summary.SUMMARY_COLUMNS["verdicts"],
    )
    mismatches = pd.DataFrame(
        [("A", "silent_shift", "small", "nulls", False, 3, 5)],
        columns=summary.SUMMARY_COLUMNS["mismatches"],
    )
    post = pd.DataFrame(
        [
            ("W1", "A", 4.0, "volume", "small", 2),
            ("W1", "A", 4.0, "null_spike", "small", 0),
        ],
        columns=summary.SUMMARY_COLUMNS["post_fault_alarms"],
    )
    return {
        "recall": recall,
        "recall_by_family": summary.empty("recall_by_family"),
        "false_alarms": false_alarms,
        "ttd": ttd,
        "operating_points": ops,
        "verdicts": verdicts,
        "mismatches": mismatches,
        "post_fault_alarms": post,
    }


META = {"seeds": [3601, 3602], "worlds": ["W1"], "ks": [2.0, 4.0], "git_sha": "abc1234"}


def test_report_has_metadata_and_verdicts_first():
    text = render_markdown(_tables(), META)
    assert "3601, 3602" in text and "abc1234" in text
    assert "**H1**, world W1: **supported**" in text
    assert "**H2**, world W1: **not supported**" in text
    assert text.index("## Verdicts") < text.index("## Recall") < text.index("## False alarms")


def test_recall_cells_use_the_interval_style():
    text = render_markdown(_tables(), META)
    assert f"3/5 (0.23{DASH}0.88)" in text  # Wilson 95% for 3 of 5
    assert f"0/5 (0.00{DASH}0.43)" in text
    # the operating points are shown next to the default k
    assert "S1 at operating point k=5" in text and "A at operating point k=6" in text
    assert f"4/5 (0.38{DASH}0.96)" in text


def test_report_lists_mismatches_false_alarms_and_post_fault_alarms():
    text = render_markdown(_tables(), META)
    assert "1 (arm, fault, severity, family) cells differ" in text
    assert "| A | silent_shift | small | nulls | silent | 3/5 |" in text
    assert "3/230 (1.30)" in text
    assert "| W1 | A | 4 | volume | small | 2 |" in text
    assert "null_spike" not in text.split("## Alarms after the fault window")[1]


def test_report_renders_from_empty_tables_without_error():
    empty = {name: summary.empty(name) for name in summary.SUMMARY_COLUMNS}
    text = render_markdown(empty, {})
    assert "No verdicts computed." in text
    assert "Every cell matches the pre-registered prediction." in text


def test_write_results_writes_report_and_one_csv_per_table(tmp_path):
    tables = _tables()
    report = write_results(tables, META, tmp_path / "out")
    assert report.name == "REPORT.md" and report.read_text(encoding="utf-8").startswith("# ")
    for name, df in tables.items():
        back = pd.read_csv(tmp_path / "out" / f"{name}.csv")
        assert list(back.columns) == list(df.columns)
        assert len(back) == len(df)
