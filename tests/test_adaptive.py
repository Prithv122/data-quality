from dq.adaptive import ThresholdRule, baseline_stats, evaluate_series
from dq.types import MetricValue


def _series(values):
    return [MetricValue(float(v)) for v in values]


def _rule(mode, k=4.0, side="two_sided", **kw):
    return ThresholdRule(k=k, side=side, mode=mode, warmup=5, window=5, min_baseline=3, **kw)


def test_baseline_stats_uses_scaled_mad_and_floors():
    med, scale = baseline_stats([10, 11, 12, 13, 14], _rule("rolling"))
    assert med == 12
    assert abs(scale - 1.4826) < 1e-9
    _, floored = baseline_stats([5, 5, 5], _rule("rolling", floor=0.5))
    assert floored == 0.5
    _, rel = baseline_stats([100, 100, 100], _rule("rolling", floor_relative=0.01))
    assert rel == 1.0


def test_warmup_is_never_judged():
    out = evaluate_series(_series([1, 1000, 1, 1, 1, 1]), _rule("rolling", floor=1))
    assert [d.status for d in out[:5]] == ["skipped"] * 5
    assert all(d.detail == "warm-up" for d in out[:5])


def test_frozen_ignores_drift_rolling_follows_it():
    # Warm-up flat at 100, then a slow climb of 0.5 per batch. Scale floor 1, k 4.
    values = [100] * 5 + [100 + 0.5 * i for i in range(1, 15)]
    frozen = evaluate_series(_series(values), _rule("frozen", floor=1))
    rolling = evaluate_series(_series(values), _rule("rolling", floor=1))
    assert any(d.status == "fail" for d in frozen[5:])
    assert all(d.status == "pass" for d in rolling[5:])


def test_alarmed_batches_do_not_enter_the_rolling_baseline():
    values = [100] * 5 + [200] * 6
    out = evaluate_series(_series(values), _rule("rolling", floor=1))
    # A persistent fault keeps alarming because it never becomes the baseline.
    assert [d.status for d in out[5:]] == ["fail"] * 6


def test_upper_side_ignores_drops():
    values = [0.02] * 5 + [0.0, 0.5]
    out = evaluate_series(_series(values), _rule("rolling", side="upper", floor=0.005))
    assert [d.status for d in out[5:]] == ["pass", "fail"]


def test_error_and_skipped_metrics_pass_through_and_stay_out_of_baseline():
    metrics = [
        *_series([10] * 5),
        MetricValue(None, "error", "column temp_max_c missing"),
        MetricValue(None, "skipped", "empty batch"),
        MetricValue(10.0),
    ]
    out = evaluate_series(metrics, _rule("rolling", floor=1))
    assert [d.status for d in out[5:]] == ["error", "skipped", "pass"]
    assert out[5].detail == "column temp_max_c missing"


def test_insufficient_baseline_is_skipped():
    metrics = [MetricValue(None, "skipped")] * 5 + _series([10])
    for mode in ("frozen", "rolling"):
        out = evaluate_series(metrics, _rule(mode, floor=1))
        assert out[5].status == "skipped"
        assert out[5].detail == "insufficient baseline"
