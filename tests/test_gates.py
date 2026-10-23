import json

import pytest

from dq import gates
from dq.cli import _unpassed_gates


def test_g1_passes_on_the_fixture(tmp_path):
    passed, detail = gates.g1_contract(tmp_path)
    assert passed, detail


def test_g5_passes_on_a_real_lake_table(tmp_path):
    pytest.importorskip("lakehouse")
    passed, detail = gates.g5_lake(tmp_path, rows=1_000)
    assert passed, detail


def test_a_crashing_gate_is_a_failed_gate(tmp_path, monkeypatch):
    def boom(_workdir):
        raise RuntimeError("no warehouse")

    monkeypatch.setitem(gates.GATES, "G1", boom)
    out = gates.run_gates(tmp_path / "work", only=("G1",))
    assert out["G1"] == {"passed": False, "detail": "RuntimeError: no warehouse"}
    assert not (tmp_path / "work").exists()


def _record(raw, sha, **overrides):
    record = {
        "git_sha": sha,
        "gates": {g: {"passed": True, "detail": "ok"} for g in ("G1", "G4", "G5")},
    }
    record["gates"].update(overrides)
    (raw / "gates.json").write_text(json.dumps(record), encoding="utf-8")


def test_verdicts_need_every_gate_on_the_same_clean_commit(tmp_path):
    manifest = {"git_sha": "abc1234", "g3": {"passed": True, "detail": ""}}
    assert _unpassed_gates(tmp_path, manifest) == ["G1, G4, G5 not run (dq gates)"]

    _record(tmp_path, "abc1234")
    assert _unpassed_gates(tmp_path, manifest) == []

    _record(tmp_path, "def5678")
    assert any("gates ran on def5678" in m for m in _unpassed_gates(tmp_path, manifest))

    _record(tmp_path, "abc1234", G4={"passed": False, "detail": "3 mismatches"})
    assert _unpassed_gates(tmp_path, manifest) == ["G4: 3 mismatches"]

    dirty = {"git_sha": "abc1234-dirty", "g3": None}
    problems = _unpassed_gates(tmp_path, dirty)
    assert any("uncommitted" in m for m in problems)
    assert any("G3" in m for m in problems)
