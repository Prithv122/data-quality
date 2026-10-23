import json

import pandas as pd
import pytest

from dq.cli import main
from dq.simulate import run_grid


def test_fixture_matches_the_contract_through_the_cli(tmp_path, capsys):
    db = tmp_path / "w.duckdb"
    assert main(["build-fixture", "--out", str(db)]) == 0
    assert main(["contract", "--fixture", str(db)]) == 0
    assert "contract OK" in capsys.readouterr().out


def test_run_on_the_clean_fixture_raises_nothing(tmp_path, capsys):
    db = tmp_path / "w.duckdb"
    main(["build-fixture", "--out", str(db)])
    out_json = tmp_path / "results.json"
    # One check time just after #22's last load: freshness is 4 h.
    code = main(
        ["run", "--fixture", str(db), "--check-time", "2025-01-01T06:00:00+00:00",
         "--json", str(out_json)]
    )  # fmt: skip
    assert code == 0
    results = json.loads(out_json.read_text(encoding="utf-8"))
    assert results
    assert not any(r["alarm"] for r in results)


def test_run_flags_a_stale_fixture(tmp_path):
    db = tmp_path / "w.duckdb"
    main(["build-fixture", "--out", str(db)])
    assert main(["run", "--fixture", str(db), "--check-time", "2025-01-20T06:00:00+00:00"]) == 1


@pytest.mark.slow
def test_g3_same_seed_gives_identical_results():
    first = run_grid((3601,), ("W1",), (4.0,))
    second = run_grid((3601,), ("W1",), (4.0,))
    pd.testing.assert_frame_equal(first.results, second.results)
    assert not any(first.verify_problems.values())
