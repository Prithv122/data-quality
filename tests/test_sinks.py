"""Alert sinks: stdout, JSON file, Slack payload and the webhook flow (never a real request)."""

from __future__ import annotations

import io
import json

import pytest

from dq import sinks
from dq.types import CheckResult


def r(check, status, batch=3, observed=1.0, expected="== 0", detail="", family="nulls", snap=None):
    return CheckResult(
        check=check,
        family=family,
        table="raw.weather_daily",
        batch=batch,
        status=status,
        observed=observed,
        expected=expected,
        detail=detail,
        snapshot_id=snap,
    )


RESULTS = [
    r("not_null.city_id", "fail", observed=2.0, detail="2 rows"),
    r("volume.rows", "pass", family="volume"),
    r("schema.contract", "error", observed=None, family="schema", detail="column gone"),
    r("null_rate.precip_mm", "warn", observed=0.25, expected="<= 0.04"),
    r("freshness.lag", "skipped", family="freshness"),
]


def test_alerts_keep_only_alarms_in_order():
    found = sinks.alerts(RESULTS)
    assert [a.check for a in found] == [
        "not_null.city_id",
        "schema.contract",
        "null_rate.precip_mm",
    ]
    assert [a.status for a in found] == ["fail", "error", "warn"]
    a = found[0]
    assert (a.family, a.table, a.batch, a.observed, a.expected) == (
        "nulls",
        "raw.weather_daily",
        3,
        2.0,
        "== 0",
    )
    assert sinks.alerts([r("x", "pass"), r("y", "skipped")]) == []


def test_stdout_has_one_line_per_alarm_and_a_count():
    buf = io.StringIO()
    sinks.to_stdout(RESULTS, stream=buf)
    lines = buf.getvalue().splitlines()
    assert len(lines) == 4
    assert lines[0].startswith("ALARM [fail] not_null.city_id (nulls) raw.weather_daily, batch 3")
    assert "observed 2" in lines[0] and "expected == 0" in lines[0] and "2 rows" in lines[0]
    assert "observed" not in lines[1] and "column gone" in lines[1]
    assert lines[-1] == "3 alarms in 5 checks"
    assert "volume.rows" not in buf.getvalue()


def test_stdout_with_no_alarms_prints_only_the_count(capsys):
    sinks.to_stdout([r("a", "pass")])
    assert capsys.readouterr().out == "0 alarms in 1 check\n"


def test_stdout_mentions_snapshot():
    buf = io.StringIO()
    sinks.to_stdout([r("a", "fail", batch=None, snap=77)], stream=buf)
    first = buf.getvalue().splitlines()[0]
    assert "snapshot 77" in first and "batch" not in first


def test_json_file_holds_every_result_with_alarm_flag(tmp_path):
    path = sinks.write_json(RESULTS, tmp_path / "out" / "results.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data) == 5
    assert [d["alarm"] for d in data] == [True, False, True, True, False]
    assert data[0]["check"] == "not_null.city_id" and data[0]["observed"] == 2.0
    assert data[2]["observed"] is None
    assert set(data[0]) == {
        "check",
        "family",
        "table",
        "batch",
        "status",
        "observed",
        "expected",
        "detail",
        "snapshot_id",
        "alarm",
    }


def test_json_turns_nan_into_null(tmp_path):
    path = sinks.write_json([r("a", "fail", observed=float("nan"))], tmp_path / "r.json")
    assert json.loads(path.read_text(encoding="utf-8"))[0]["observed"] is None


def test_slack_payload_structure():
    payload = sinks.slack_payload(RESULTS, "smoke run 7")
    blocks = payload["blocks"]
    assert blocks[0]["type"] == "header"
    assert blocks[0]["text"] == {"type": "plain_text", "text": "Data quality: smoke run 7"}
    assert blocks[1]["type"] == "section"
    summary_text = blocks[1]["text"]["text"]
    assert "3 alarms in 5 checks" in summary_text
    assert "fail 1" in summary_text and "error 1" in summary_text and "warn 1" in summary_text
    assert "pass 1" in summary_text and "skipped 1" in summary_text
    assert len(blocks) == 2 + 3  # one section per alert
    assert "not_null.city_id" in blocks[2]["text"]["text"]
    assert "`fail`" in blocks[2]["text"]["text"]
    assert payload["text"].startswith("Data quality: smoke run 7")
    assert payload == sinks.slack_payload(RESULTS, "smoke run 7")  # deterministic
    json.dumps(payload)


def test_slack_payload_truncates_with_a_tail():
    many = [r(f"c{i}", "fail") for i in range(30)]
    payload = sinks.slack_payload(many, "big", max_items=5)
    blocks = payload["blocks"]
    assert len(blocks) == 2 + 5 + 1
    assert blocks[-1]["text"]["text"] == "and 25 more"
    exactly = sinks.slack_payload(many[:5], "big", max_items=5)
    assert len(exactly["blocks"]) == 2 + 5
    default = sinks.slack_payload(many, "big")
    assert default["blocks"][-1]["text"]["text"] == "and 10 more"


def test_slack_payload_stays_inside_slack_limits():
    many = [r(f"c{i}", "fail", detail="x" * 5000) for i in range(100)]
    payload = sinks.slack_payload(many, "big", max_items=500)
    assert len(payload["blocks"]) <= 50
    assert all(len(b["text"]["text"]) <= 3000 for b in payload["blocks"][1:])


def test_slack_payload_escapes_markup_in_user_text():
    payload = sinks.slack_payload([r("a<b>", "fail", detail="<!channel> & more")], "run")
    text = payload["blocks"][2]["text"]["text"]
    assert "<!channel>" not in text and "&lt;!channel&gt; &amp; more" in text


def test_send_slack_posts_once_with_url_from_env(monkeypatch):
    monkeypatch.setenv(sinks.WEBHOOK_ENV, "https://hooks.example.test/T/B/x")
    transport = sinks.RecordingTransport(status=200)
    assert sinks.send_slack(RESULTS, "run 1", transport) == 200
    assert len(transport.calls) == 1
    url, payload = transport.calls[0]
    assert url == "https://hooks.example.test/T/B/x"
    assert payload == sinks.slack_payload(RESULTS, "run 1")


def test_send_slack_explicit_url_wins_and_status_is_returned(monkeypatch):
    monkeypatch.setenv(sinks.WEBHOOK_ENV, "https://env.example.test/x")
    transport = sinks.RecordingTransport(status=404)
    assert sinks.send_slack(RESULTS, "run", transport, url="https://arg.example.test/y") == 404
    assert transport.calls[0][0] == "https://arg.example.test/y"


def test_send_slack_without_url_does_nothing(monkeypatch):
    monkeypatch.delenv(sinks.WEBHOOK_ENV, raising=False)
    transport = sinks.RecordingTransport()
    assert sinks.send_slack(RESULTS, "run", transport) is None
    assert transport.calls == []


def test_send_slack_without_alarms_does_nothing(monkeypatch):
    monkeypatch.setenv(sinks.WEBHOOK_ENV, "https://hooks.example.test/x")
    transport = sinks.RecordingTransport()
    assert sinks.send_slack([r("a", "pass"), r("b", "skipped")], "run", transport) is None
    assert transport.calls == []


def test_urllib_transport_refuses_non_http_urls():
    with pytest.raises(ValueError, match="http"):
        sinks.UrllibTransport().post("file:///etc/passwd", {})
