"""Where alarms go: stdout, a JSON file and a Slack incoming webhook.

The webhook URL comes from the environment (`DQ_SLACK_WEBHOOK_URL`) or an argument, never from
a file in the repository. The HTTP call sits behind a small transport interface, so tests and
dry runs use `RecordingTransport` and never touch the network.
"""

from __future__ import annotations

import json
import math
import os
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol, TextIO

from dq.types import CheckResult

WEBHOOK_ENV = "DQ_SLACK_WEBHOOK_URL"
# Slack allows 50 blocks per message and 3,000 characters per section text.
_MAX_BLOCKS = 50
_SECTION_CHARS = 2_900
_STATUS_ORDER = ("fail", "error", "warn", "pass", "skipped")


@dataclass(frozen=True)
class Alert:
    """An alarming check result, flattened for people and messages."""

    check: str
    family: str
    table: str
    batch: int | None
    snapshot_id: int | None
    status: str
    observed: float | None
    expected: str
    detail: str

    @classmethod
    def from_result(cls, r: CheckResult) -> Alert:
        return cls(
            check=r.check,
            family=r.family,
            table=r.table,
            batch=r.batch,
            snapshot_id=r.snapshot_id,
            status=r.status,
            observed=r.observed,
            expected=r.expected,
            detail=r.detail,
        )

    def where(self) -> str:
        parts = [self.table]
        if self.batch is not None:
            parts.append(f"batch {self.batch}")
        if self.snapshot_id is not None:
            parts.append(f"snapshot {self.snapshot_id}")
        return ", ".join(parts)

    def line(self) -> str:
        head = f"ALARM [{self.status}] {self.check} ({self.family}) {self.where()}"
        rest = []
        if self.observed is not None:
            rest.append(f"observed {_num(self.observed)}")
        if self.expected:
            rest.append(f"expected {self.expected}")
        if self.detail:
            rest.append(self.detail)
        return " | ".join([head, *rest])


def _num(x: float | None) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/a"
    return f"{x:.6g}"


def alerts(results: list[CheckResult]) -> list[Alert]:
    return [Alert.from_result(r) for r in results if r.alarm]


def _summary_line(results: list[CheckResult]) -> str:
    n_alarms = sum(1 for r in results if r.alarm)
    n = len(results)
    return f"{n_alarms} alarm{'s' if n_alarms != 1 else ''} in {n} check{'s' if n != 1 else ''}"


def to_stdout(results: list[CheckResult], stream: TextIO | None = None) -> None:
    """One line per alarm, nothing for passes, then a count line."""
    out = stream if stream is not None else sys.stdout
    for alert in alerts(results):
        print(alert.line(), file=out)
    print(_summary_line(results), file=out)


def _clean(value: float | None) -> float | None:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return None
    return value


def write_json(results: list[CheckResult], path: str | Path) -> Path:
    """Every result (not only alarms) as a JSON list, each with an `alarm` field."""
    records = []
    for r in results:
        rec = asdict(r)
        rec["observed"] = _clean(rec["observed"])
        rec["alarm"] = r.alarm
        records.append(rec)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(records, indent=2, allow_nan=False) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def _mrkdwn(text: str) -> str:
    """Escape the three characters Slack treats as markup."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _section(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text[:_SECTION_CHARS]}}


def _alert_text(a: Alert) -> str:
    head = f"*{_mrkdwn(a.check)}* ({_mrkdwn(a.family)}) `{a.status}`"
    facts = [_mrkdwn(a.where())]
    if a.observed is not None:
        facts.append(f"observed {_num(a.observed)}")
    if a.expected:
        facts.append(f"expected {_mrkdwn(a.expected)}")
    text = head + "\n" + " · ".join(facts)
    if a.detail:
        text += "\n" + _mrkdwn(a.detail)
    return text


def slack_payload(results: list[CheckResult], run_label: str, max_items: int = 20) -> dict:
    """Slack Block Kit message: header, counts by status, one section per alert, a tail."""
    found = alerts(results)
    counts = {s: sum(1 for r in results if r.status == s) for s in _STATUS_ORDER}
    shown_counts = ", ".join(f"{s} {counts[s]}" for s in _STATUS_ORDER if counts[s])
    limit = max(0, min(max_items, _MAX_BLOCKS - 3))
    title = f"Data quality: {run_label}"[:150]
    summary_text = f"*{_summary_line(results)}*" + (f"\n{shown_counts}" if shown_counts else "")
    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": title}},
        _section(summary_text),
    ]
    blocks.extend(_section(_alert_text(a)) for a in found[:limit])
    if len(found) > limit:
        blocks.append(_section(f"and {len(found) - limit} more"))
    return {"text": f"{title} - {_summary_line(results)}", "blocks": blocks}


class SlackTransport(Protocol):
    def post(self, url: str, payload: dict) -> int:
        """POST the payload as JSON and return the HTTP status code."""
        ...


class UrllibTransport:
    """The real thing: a JSON POST with a 10 second timeout."""

    timeout = 10

    def post(self, url: str, payload: dict) -> int:
        if not url.lower().startswith(("https://", "http://")):
            raise ValueError("webhook URL must be http or https")
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return int(response.status)
        except urllib.error.HTTPError as err:
            return int(err.code)


class RecordingTransport:
    """Stores what would have been posted; for tests and dry runs."""

    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.calls: list[tuple[str, dict]] = []

    def post(self, url: str, payload: dict) -> int:
        self.calls.append((url, payload))
        return self.status


def send_slack(
    results: list[CheckResult],
    run_label: str,
    transport: SlackTransport,
    url: str | None = None,
) -> int | None:
    """Post the alarms; returns the HTTP status, or None when nothing was sent.

    Nothing is sent when no URL is configured or when there are no alarms.
    """
    target = url or os.environ.get(WEBHOOK_ENV)
    if not target:
        return None
    if not any(r.alarm for r in results):
        return None
    return transport.post(target, slack_payload(results, run_label))
