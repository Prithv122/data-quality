"""Load `checks.yml` into typed definitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from dq.adaptive import Mode, ThresholdRule
from dq.spec import ARMS, Arm
from dq.types import FAMILIES, Family

DEFAULT_CHECKS = Path(__file__).resolve().parents[2] / "checks.yml"
Kind = Literal["static", "threshold"]

_TOP_KEYS = {"id", "family", "kind", "rule", "metric", "arms"}


@dataclass(frozen=True)
class CheckDef:
    id: str
    family: Family
    kind: Kind
    arms: frozenset[Arm]
    rule: str | None = None  # static checks
    metric: str | None = None  # threshold checks
    params: dict[str, Any] = field(default_factory=dict)

    def threshold_rule(self, k: float, mode: Mode) -> ThresholdRule:
        if self.kind != "threshold":
            raise ValueError(f"{self.id} is not a threshold check")
        return ThresholdRule(
            k=k,
            side=self.params["side"],
            mode=mode,
            floor=float(self.params.get("floor", 0.0)),
            floor_relative=float(self.params.get("floor_relative", 0.0)),
        )


@dataclass(frozen=True)
class CheckSuite:
    table: str
    parent: str  # "raw.cities.city_id"
    checks: tuple[CheckDef, ...]

    def for_arm(self, arm: Arm) -> tuple[CheckDef, ...]:
        return tuple(c for c in self.checks if arm in c.arms)

    def by_id(self, check_id: str) -> CheckDef:
        for c in self.checks:
            if c.id == check_id:
                return c
        raise KeyError(check_id)


def load_checks(path: str | Path = DEFAULT_CHECKS) -> CheckSuite:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    checks = []
    seen: set[str] = set()
    for item in raw["checks"]:
        cid = item["id"]
        if cid in seen:
            raise ValueError(f"duplicate check id {cid}")
        seen.add(cid)
        if item["family"] not in FAMILIES:
            raise ValueError(f"{cid}: unknown family {item['family']}")
        arms = frozenset(item["arms"])
        if not arms <= set(ARMS):
            raise ValueError(f"{cid}: unknown arm in {sorted(arms)}")
        kind = item["kind"]
        if kind == "static" and "rule" not in item:
            raise ValueError(f"{cid}: static check needs a rule")
        if kind == "threshold" and ("metric" not in item or "side" not in item):
            raise ValueError(f"{cid}: threshold check needs metric and side")
        checks.append(
            CheckDef(
                id=cid,
                family=item["family"],
                kind=kind,
                arms=arms,
                rule=item.get("rule"),
                metric=item.get("metric"),
                params={k: v for k, v in item.items() if k not in _TOP_KEYS},
            )
        )
    return CheckSuite(table=raw["table"], parent=raw["parent"], checks=tuple(checks))
