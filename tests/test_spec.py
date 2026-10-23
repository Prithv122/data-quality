"""The frozen experiment definition is internally consistent."""

import json
from pathlib import Path

from dq import spec
from dq.checkdefs import load_checks

CONTRACT = Path(__file__).parent / "fixtures" / "contract_22.json"


def test_condition_count_is_the_locked_120():
    assert len(spec.FAULT_TYPES) * len(spec.SEVERITIES) * len(spec.SEEDS) == 120
    assert set(spec.FAULTS) == set(spec.FAULT_TYPES)
    assert all(set(v) == set(spec.SEVERITIES) for v in spec.FAULTS.values())


def test_onset_window_fits_inside_the_scored_batches():
    lo, hi = spec.ONSET_RANGE
    assert lo == spec.EVAL_FIRST
    assert hi + spec.FAULT_WINDOW - 1 == spec.N_BATCHES


def test_expected_matrix_covers_every_cell():
    for arm in spec.ARMS:
        assert set(spec.EXPECTED[arm]) == set(spec.FAULT_TYPES)
        for fault in spec.FAULT_TYPES:
            assert set(spec.EXPECTED[arm][fault]) == set(spec.SEVERITIES)


def test_checks_yml_matches_the_spec():
    suite = load_checks()
    ids = {c.id for c in suite.checks}
    assert set(spec.OPERATING_POINT_CHECKS) <= ids
    # S0 is exactly #22's own tests: no thresholds, no contract check.
    assert all(c.kind == "static" for c in suite.for_arm("S0"))
    assert "schema.contract" not in {c.id for c in suite.for_arm("S0")}
    # S1 and A carry the same checks; they differ only in baseline mode.
    assert {c.id for c in suite.for_arm("S1")} == {c.id for c in suite.for_arm("A")}
    rule = suite.by_id("volume.rows").threshold_rule(4.0, "rolling")
    assert rule.side == "two_sided"
    assert rule.floor_relative == 0.01


def test_contract_names_the_pinned_upstream():
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert contract["upstream"]["sha"] == spec.UPSTREAM_22_SHA
    assert contract["freshness"]["warn_after_days"] * 24 == 72
    assert set(contract["tables"]["raw.weather_daily"]["columns"]) >= {
        "temp_max_c",
        "precip_mm",
        "ingested_at",
    }


def test_seed_cities_have_climates():
    assert len(spec.CLIMATES) == 10
