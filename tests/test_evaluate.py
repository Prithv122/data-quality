"""Evaluation over a hand-made results table whose answers are worked out by hand.

World W1 has two seeds. Clean-stream alarms (scored batches 15-30, so 32 batch-checks per check
and arm over both seeds):

  seed 1: volume.rows frozen k=2 at 16, 17; k=4 at 16; null_rate.temp_max_c frozen k=2 at 19
  seed 2: volume.rows frozen k=2 at 21; rolling k=2 at 27

Every faulted stream repeats its seed's clean alarms (the twin), plus the extras below.
"""

import math

import pandas as pd
import pytest

from dq import evaluate, spec, summary
from dq.checkdefs import load_checks
from dq.simulate import CLEAN_ID
from dq.types import FAMILIES

SUITE = load_checks()
FAMILY = {c.id: c.family for c in SUITE.checks}
BATCHES = range(15, 31)
KS = (2.0, 4.0, 8.0)
STATIC_CHECKS = ("unique.city_id_date",)
THRESHOLD_CHECKS = ("volume.rows", "null_rate.temp_max_c", "null_rate.precip_mm")
NAN = float("nan")

CLEAN_ALARMS = {
    ("W1", 1): [
        ("volume.rows", "frozen", 2.0, 16),
        ("volume.rows", "frozen", 2.0, 17),
        ("volume.rows", "frozen", 4.0, 16),
        ("null_rate.temp_max_c", "frozen", 2.0, 19),
    ],
    ("W1", 2): [
        ("volume.rows", "frozen", 2.0, 21),
        ("volume.rows", "rolling", 2.0, 27),
    ],
    # W0: the frozen volume check is noisy at every k
    ("W0", 1): [("volume.rows", "frozen", k, 16) for k in KS],
}

# stream id -> (world, seed, fault, severity, onset, extra alarms the clean twin lacks)
FAULTED = {
    "W1-1-volume-small": (
        "W1",
        1,
        "volume",
        "small",
        18,
        [
            ("volume.rows", "frozen", 2.0, 18),
            ("volume.rows", "frozen", 2.0, 19),
            ("volume.rows", "frozen", 4.0, 20),
            ("volume.rows", "rolling", 2.0, 18),
            ("volume.rows", "rolling", 4.0, 18),
            ("volume.rows", "rolling", 4.0, 19),
            ("volume.rows", "rolling", 8.0, 18),
            ("volume.rows", "rolling", 4.0, 26),  # after the window 18..24
            ("volume.rows", "frozen", 2.0, 27),  # after the window
        ],
    ),
    "W1-2-volume-small": ("W1", 2, "volume", "small", 18, [("volume.rows", "frozen", 2.0, 22)]),
    "W1-1-duplicates-small": (
        "W1",
        1,
        "duplicates",
        "small",
        24,
        [
            ("unique.city_id_date", "static", None, 24),
            ("unique.city_id_date", "static", None, 26),
            ("volume.rows", "rolling", 2.0, 25),
        ],
    ),
    "W1-1-silent_shift-small": ("W1", 1, "silent_shift", "small", 25, []),
}


def _stream_rows(world, seed, stream, fault, severity, onset, column, alarms):
    alarm_set = {(c, m, None if k is None else float(k), b) for c, m, k, b in alarms}
    rows = []
    for b in BATCHES:
        for check in STATIC_CHECKS:
            hit = (check, "static", None, b) in alarm_set
            rows.append((b, check, "static", NAN, hit))
        for check in THRESHOLD_CHECKS:
            for mode in ("frozen", "rolling"):
                for k in KS:
                    rows.append((b, check, mode, k, (check, mode, k, b) in alarm_set))
    df = pd.DataFrame(rows, columns=["batch", "check", "mode", "k", "alarm"])
    df["family"] = df["check"].map(FAMILY)
    df["status"] = df["alarm"].map({True: "fail", False: "pass"})
    df["observed"] = 0.0
    tags = {
        "world": world,
        "seed": seed,
        "stream": stream,
        "fault": fault,
        "severity": severity,
        "onset": onset,
        "column": column,
    }
    for i, (name, value) in enumerate(tags.items()):
        df.insert(i, name, value)
    return df


def build_results(extra_faulted=None, clean_extra=None):
    """The hand-made results table. `extra_faulted` adds alarms per faulted stream id."""
    extra_faulted = extra_faulted or {}
    parts = []
    for (world, seed), alarms in CLEAN_ALARMS.items():
        alarms = alarms + (clean_extra or {}).get((world, seed), [])
        parts.append(_stream_rows(world, seed, CLEAN_ID, "none", "none", NAN, None, alarms))
    for stream, (world, seed, fault, severity, onset, extras) in FAULTED.items():
        alarms = CLEAN_ALARMS[(world, seed)] + extras + extra_faulted.get(stream, [])
        parts.append(_stream_rows(world, seed, stream, fault, severity, onset, None, alarms))
    return pd.concat(parts, ignore_index=True)


def build_conditions():
    rows = [
        {
            "id": stream,
            "world": world,
            "seed": seed,
            "fault": fault,
            "severity": severity,
            "onset": onset,
            "column": None,
        }
        for stream, (world, seed, fault, severity, onset, _) in FAULTED.items()
    ]
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def results():
    return build_results()


@pytest.fixture(scope="module")
def conds():
    return build_conditions()


@pytest.fixture(scope="module")
def tables(results, conds):
    return evaluate.evaluate_tables(results, conds, SUITE)


def _pick(df, **kw):
    mask = pd.Series(True, index=df.index)
    for col, value in kw.items():
        if isinstance(value, float) and math.isnan(value):
            mask &= df[col].isna()
        else:
            mask &= df[col] == value
    out = df[mask]
    assert len(out) == 1, (kw, len(out))
    return out.iloc[0]


# --- wilson ------------------------------------------------------------------------------


def test_wilson_known_values():
    lo, hi = evaluate.wilson(0, 5)
    assert lo == 0.0 and hi == pytest.approx(0.4345, abs=1e-3)
    lo, hi = evaluate.wilson(5, 5)
    assert hi == 1.0 and lo == pytest.approx(0.5655, abs=1e-3)
    lo, hi = evaluate.wilson(1, 2)
    assert (lo, hi) == (pytest.approx(0.0945, abs=1e-3), pytest.approx(0.9055, abs=1e-3))
    lo, hi = evaluate.wilson(3, 5)
    assert (lo, hi) == (pytest.approx(0.2307, abs=1e-3), pytest.approx(0.8824, abs=1e-3))
    assert all(math.isnan(x) for x in evaluate.wilson(0, 0))


# --- arms and attribution ----------------------------------------------------------------


def test_arm_views(results):
    s0 = evaluate.arm_view(results, "S0", suite=SUITE)
    assert set(s0["mode"]) == {"static"} and set(s0["check"]) == set(STATIC_CHECKS)
    s1 = evaluate.arm_view(results, "S1", 4.0, suite=SUITE)
    assert set(s1["mode"]) == {"static", "frozen"}
    assert set(s1["k"].dropna()) == {4.0}
    assert (s1["arm"] == "S1").all()
    a = evaluate.arm_view(results, "A", 8.0, suite=SUITE)
    assert set(a["mode"]) == {"static", "rolling"} and set(a["k"].dropna()) == {8.0}


def test_attributable_alarms_exclude_the_twins_and_stay_in_the_window(results):
    att = evaluate.attributable(results)
    assert (att["stream"] != CLEAN_ID).all()
    assert (att["batch"] >= att["onset"]).all() and (att["batch"] < att["onset"] + 7).all()

    c1 = att[att["stream"] == "W1-1-volume-small"]
    assert len(c1) == 7  # nine extras, two of them after the window
    assert not ((c1["batch"] == 19) & (c1["check"] == "null_rate.temp_max_c")).any()  # twin alarm

    c2 = att[att["stream"] == "W1-2-volume-small"]
    assert list(zip(c2["mode"], c2["k"], c2["batch"], strict=True)) == [("frozen", 2.0, 22)]
    # seed 2's own clean alarm at batch 21 sits inside the window and is not attributed

    c3 = att[att["stream"] == "W1-1-duplicates-small"]
    assert set(c3.loc[c3["mode"] == "static", "batch"]) == {24, 26}
    assert att[att["stream"] == "W1-1-silent_shift-small"].empty


def test_gates_pass_on_consistent_data(results):
    assert evaluate.gate_g6(results) == []
    assert evaluate.gate_attribution_before_onset(results) == []
    assert evaluate.gate_s0_silent_on_clean(results, SUITE) == []


def test_gates_flag_inconsistent_data():
    early = build_results(extra_faulted={"W1-1-volume-small": [("volume.rows", "frozen", 8.0, 16)]})
    problems = evaluate.gate_attribution_before_onset(early)
    assert len(problems) == 1 and "batch 16" in problems[0]

    noisy = build_results(clean_extra={("W1", 2): [("unique.city_id_date", "static", None, 20)]})
    assert len(evaluate.gate_s0_silent_on_clean(noisy, SUITE)) == 1
    assert evaluate.gate_g6(noisy) == []  # a clean stream still scores zero against itself


# --- recall ------------------------------------------------------------------------------


def test_recall_counts_and_intervals(tables):
    recall = tables["recall"]
    row = _pick(recall, arm="S1", k=2.0, fault="volume", severity="small")
    assert (row.detected, row.n, row.recall) == (2, 2, 1.0)
    row = _pick(recall, arm="S1", k=4.0, fault="volume", severity="small")
    assert (row.detected, row.n, row.recall) == (1, 2, 0.5)
    assert (row.ci_low, row.ci_high) == (
        pytest.approx(0.0945, abs=1e-3),
        pytest.approx(0.9055, abs=1e-3),
    )
    assert _pick(recall, arm="S1", k=8.0, fault="volume", severity="small").detected == 0
    assert _pick(recall, arm="A", k=2.0, fault="volume", severity="small").detected == 1
    assert _pick(recall, arm="A", k=8.0, fault="volume", severity="small").detected == 1
    # S0 has no threshold checks and k is NaN for it
    assert _pick(recall, arm="S0", k=NAN, fault="volume", severity="small").detected == 0
    for arm in ("S0", "S1", "A"):
        k = NAN if arm == "S0" else 4.0
        assert _pick(recall, arm=arm, k=k, fault="duplicates", severity="small").detected == 1
        assert _pick(recall, arm=arm, k=k, fault="silent_shift", severity="small").detected == 0
    # 3 cells x (S0 + 2 arms x 3 k)
    assert len(recall) == 3 * 7 and not recall["n"].eq(0).any()


def test_recall_by_family(tables):
    fam = tables["recall_by_family"]
    row = _pick(fam, arm="A", k=4.0, fault="volume", severity="small", family="volume")
    assert (row.detected, row.n) == (1, 2)
    assert (
        _pick(fam, arm="A", k=4.0, fault="volume", severity="small", family="nulls").detected == 0
    )
    assert (
        _pick(fam, arm="S0", k=NAN, fault="duplicates", severity="small", family="unique").detected
        == 1
    )
    assert set(fam["family"]) == set(FAMILIES)


def test_time_to_detect(tables):
    ttd = tables["ttd"]
    row = _pick(ttd, arm="S1", k=2.0, fault="volume", severity="small")
    assert (row.n_detected, row.ttd_median, row.ttd_max) == (
        2,
        2.0,
        4.0,
    )  # onset 18; first 18 and 22
    row = _pick(ttd, arm="S1", k=4.0, fault="volume", severity="small")
    assert (row.n_detected, row.ttd_median, row.ttd_max) == (1, 2.0, 2.0)
    row = _pick(ttd, arm="A", k=4.0, fault="volume", severity="small")
    assert (row.n_detected, row.ttd_median, row.ttd_max) == (1, 0.0, 0.0)
    row = _pick(ttd, arm="S0", k=NAN, fault="duplicates", severity="small")
    assert (row.n_detected, row.ttd_median) == (1, 0.0)
    row = _pick(ttd, arm="S1", k=8.0, fault="volume", severity="small")
    assert row.n_detected == 0 and math.isnan(row.ttd_median)


# --- false alarms and operating points ---------------------------------------------------


def test_false_alarms_per_check_and_arm(tables):
    fa = tables["false_alarms"]
    row = _pick(fa, world="W1", arm="S1", k=2.0, check="volume.rows")
    assert (row.alarms, row.batch_checks) == (3, 32)
    assert row.per_100 == pytest.approx(9.375)
    row = _pick(fa, world="W1", arm="A", k=2.0, check="volume.rows")
    assert (row.alarms, row.batch_checks) == (1, 32)
    row = _pick(fa, world="W1", arm="S0", k=NAN, check="unique.city_id_date")
    assert (row.alarms, row.batch_checks, row.per_100) == (0, 32, 0.0)
    # static checks repeat at every k of S1 and A, and S0 has no threshold rows
    assert _pick(fa, world="W1", arm="A", k=8.0, check="unique.city_id_date").batch_checks == 32
    assert not ((fa["arm"] == "S0") & (fa["check"] == "volume.rows")).any()
    # W0 has one clean stream
    assert _pick(fa, world="W0", arm="S1", k=4.0, check="volume.rows").batch_checks == 16


def test_warmup_batches_are_not_scored():
    early = _stream_rows("W1", 1, CLEAN_ID, "none", "none", NAN, None, [])
    early["batch"] = early["batch"] - 10  # now 5..20: batches 5..14 are warm-up
    early.loc[early["batch"] < 15, "alarm"] = True
    fa = evaluate.false_alarm_table(early, SUITE, KS)
    assert fa["alarms"].sum() == 0
    assert (fa["batch_checks"] == 6).all()  # batches 15..20


def test_operating_points(tables):
    ops = tables["operating_points"]
    # S1: k=2 -> 4/96, k=4 -> 1/96 = 1.04 (> 1), k=8 -> 0
    row = _pick(ops, world="W1", arm="S1")
    assert (row.k, row.fa_per_100, row.reached_target) == (8.0, 0.0, True)
    # A: k=2 -> 1/96 = 1.04 (> 1), k=4 -> 0
    row = _pick(ops, world="W1", arm="A")
    assert (row.k, row.fa_per_100, row.reached_target) == (4.0, 0.0, True)


def test_operating_point_when_no_k_reaches_the_target(tables):
    ops = tables["operating_points"]
    row = _pick(ops, world="W0", arm="S1")
    assert row.k == 8.0 and not row.reached_target  # 1/48 per check set at every k
    assert row.fa_per_100 == pytest.approx(100 / 48)
    row = _pick(ops, world="W0", arm="A")
    assert (row.k, row.reached_target) == (2.0, True)


# --- verdicts ----------------------------------------------------------------------------


def test_h1_counts_and_verdict(tables):
    verdicts = tables["verdicts"]
    h1 = _pick(verdicts, hypothesis="H1", world="W1")
    # A at k=4 detects the first volume condition; S1 at k=8 detects neither
    assert h1.verdict == "supported"
    assert "a = 1" in h1.detail and "s = 0" in h1.detail
    assert "k_A = 4" in h1.detail and "k_S1 = 8" in h1.detail
    w0 = _pick(verdicts, hypothesis="H1", world="W0")
    assert w0.verdict == "reported" and "does not decide" in w0.detail


def test_h1_is_not_supported_when_frozen_catches_up(conds):
    extra = {"W1-2-volume-small": [("volume.rows", "frozen", 8.0, 22)]}
    tab = evaluate.evaluate_tables(build_results(extra), conds, SUITE)
    h1 = _pick(tab["verdicts"], hypothesis="H1", world="W1")
    assert h1.verdict == "not supported"
    assert "a = 1" in h1.detail and "s = 1" in h1.detail


def test_h2_supported_and_broken(tables, conds):
    h2 = _pick(tables["verdicts"], hypothesis="H2", world="W1")
    assert h2.verdict == "supported"
    assert "S0 0/1" in h2.detail and "A 0/1" in h2.detail

    extra = {"W1-1-silent_shift-small": [("null_rate.precip_mm", "rolling", 4.0, 26)]}
    tab = evaluate.evaluate_tables(build_results(extra), conds, SUITE)
    h2 = _pick(tab["verdicts"], hypothesis="H2", world="W1")
    assert h2.verdict == "not supported"
    assert "A 1/1" in h2.detail and "null_rate.precip_mm" in h2.detail


# --- mismatches and post-fault alarms ----------------------------------------------------


def test_expected_mismatches(results, conds):
    att = evaluate.attributable(results)
    assert evaluate.expected_mismatches(att, conds, SUITE, majority=1).empty

    extra = {"W1-1-silent_shift-small": [("null_rate.precip_mm", "rolling", 4.0, 26)]}
    att2 = evaluate.attributable(build_results(extra))
    out = evaluate.expected_mismatches(att2, conds, SUITE, majority=1)
    assert len(out) == 1
    row = out.iloc[0]
    assert (row.arm, row.fault, row.severity, row.family) == ("A", "silent_shift", "small", "nulls")
    assert (bool(row.predicted), row.seeds_detected, row.n) == (False, 1, 1)

    # with the real majority (3 of 5) a single seed can never confirm a prediction
    strict = evaluate.expected_mismatches(att, conds, SUITE)
    assert not strict.empty and (strict["seeds_detected"] < spec.MAJORITY).all()
    assert strict["predicted"].all()


def test_post_fault_alarms(tables):
    post = tables["post_fault_alarms"]
    assert _pick(post, arm="A", k=4.0, fault="volume", severity="small").alarms == 1
    assert _pick(post, arm="S1", k=2.0, fault="volume", severity="small").alarms == 1
    assert _pick(post, arm="A", k=2.0, fault="volume", severity="small").alarms == 0  # twin has it
    assert _pick(post, arm="S0", k=NAN, fault="duplicates", severity="small").alarms == 0


# --- the bundle --------------------------------------------------------------------------


def test_every_table_has_the_declared_columns(tables):
    assert set(tables) == {
        "recall",
        "recall_by_family",
        "false_alarms",
        "ttd",
        "operating_points",
        "verdicts",
        "mismatches",
        "post_fault_alarms",
    }
    for name, df in tables.items():
        assert list(df.columns) == list(summary.SUMMARY_COLUMNS[name]), name


def test_results_are_ordered_by_arm_then_k(tables):
    recall = tables["recall"]
    assert list(recall["arm"].drop_duplicates()) == ["S0", "S1", "A"]
    s1 = recall[recall["arm"] == "S1"]
    assert list(s1["k"].drop_duplicates()) == [2.0, 4.0, 8.0]
