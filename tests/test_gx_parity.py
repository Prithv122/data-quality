"""Gate G4 machinery: Great Expectations verdicts on hand-made batches.

Each case lists the verdict the pre-registered semantics require for every static check, written
by hand (not taken from the check code), and the GX result must equal it.
"""

from __future__ import annotations

import datetime as dt
import json
import socket
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("great_expectations")

from dq import gx_parity
from dq.checkdefs import load_checks
from dq.types import BatchContext, CheckResult

CONTRACT = json.loads(
    (Path(__file__).parent / "fixtures" / "contract_22.json").read_text(encoding="utf-8")
)["tables"]["raw.weather_daily"]["columns"]
SUITE = load_checks()
DAY = dt.date(2024, 1, 5)
CITIES = [f"city-{i:02d}" for i in range(12)]
SUPPORTED_IDS = [
    "schema.contract",
    "not_null.city_id",
    "not_null.date",
    "unique.city_id_date",
    "accepted_range.temp_max_c",
    "accepted_range.temp_min_c",
    "diurnal_non_negative",
    "relationships.city_id",
]
OK, ALARM = "pass", "alarm"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", refuse)


def clean_df() -> pd.DataFrame:
    n = len(CITIES)
    return pd.DataFrame(
        {
            "city_id": pd.Series(CITIES, dtype="str"),
            "date": [DAY] * n,
            "temp_max_c": np.linspace(10.0, 30.0, n),
            "temp_min_c": np.linspace(0.0, 15.0, n),
            "precip_mm": np.linspace(0.0, 5.0, n),
            "wind_max_kmh": np.linspace(5.0, 25.0, n),
            "ingested_at": pd.Series(
                pd.to_datetime(["2024-01-06T02:00:00Z"] * n), dtype="datetime64[us, UTC]"
            ),
        }
    )


def ctx_for(df: pd.DataFrame, loaded: set | None = None, parents: list[str] | None = None):
    earlier = {(c, DAY - dt.timedelta(days=d)) for c in CITIES for d in range(1, 5)}
    return BatchContext(
        table="raw.weather_daily",
        batch=7,
        batch_df=df,
        loaded_keys=frozenset(earlier | (loaded or set())),
        parent_keys=frozenset(CITIES if parents is None else parents),
        max_ingested_at=dt.datetime(2024, 1, 6, 2, tzinfo=dt.UTC),
        check_time=dt.datetime(2024, 1, 6, 6, tzinfo=dt.UTC),
    )


def verdicts(ctx: BatchContext) -> dict[str, CheckResult]:
    results = gx_parity.gx_static_results(SUITE, ctx, CONTRACT)
    return {r.check: r for r in results}


def expect(**alarms: str) -> dict[str, str]:
    """Everything passes except the checks named (keyword names use '__' for '.')."""
    want = dict.fromkeys(SUPPORTED_IDS, OK)
    for key, value in alarms.items():
        want[key.replace("__", ".")] = value
    return want


def dup_row() -> pd.DataFrame:
    df = clean_df()
    return pd.concat([df, df.iloc[[3]]], ignore_index=True)


def null_city() -> pd.DataFrame:
    df = clean_df()
    df.loc[2, "city_id"] = None
    return df


def hot() -> pd.DataFrame:
    df = clean_df()
    df.loc[4, "temp_max_c"] = 200.0
    return df


def inverted() -> pd.DataFrame:
    df = clean_df()
    df.loc[4, ["temp_max_c", "temp_min_c"]] = [3.0, 8.0]  # both in range, min > max
    return df


def unknown_city() -> pd.DataFrame:
    df = clean_df()
    df.loc[5, "city_id"] = "ghost-99"
    return df


def nulls_in_temps() -> pd.DataFrame:
    df = clean_df()
    df.loc[1, "temp_max_c"] = np.nan
    df.loc[2, "temp_min_c"] = np.nan
    df.loc[3, ["temp_max_c", "temp_min_c"]] = np.nan
    return df


def boundary() -> pd.DataFrame:
    df = clean_df()
    df.loc[0, ["temp_max_c", "temp_min_c"]] = [60.0, -90.0]  # inclusive bounds
    df.loc[1, ["temp_max_c", "temp_min_c"]] = [10.0, 10.0]  # equal passes (>=)
    return df


DROPPED = expect(
    schema__contract=ALARM,
    accepted_range__temp_max_c="error",
    diurnal_non_negative="error",
)

CASES: list[tuple[str, pd.DataFrame, dict, dict[str, str]]] = [
    ("clean", clean_df(), {}, expect()),
    ("nulls in temperatures", nulls_in_temps(), {}, expect()),
    ("range and pair boundaries", boundary(), {}, expect()),
    ("null city_id", null_city(), {}, expect(not_null__city_id=ALARM)),
    ("duplicate row", dup_row(), {}, expect(unique__city_id_date=ALARM)),
    (
        "key already loaded",
        clean_df(),
        {"loaded": {(CITIES[3], DAY)}},
        expect(unique__city_id_date=ALARM),
    ),
    ("temp_max_c = 200", hot(), {}, expect(accepted_range__temp_max_c=ALARM)),
    ("min greater than max", inverted(), {}, expect(diurnal_non_negative=ALARM)),
    ("unknown city id", unknown_city(), {}, expect(relationships__city_id=ALARM)),
    ("dropped temp_max_c", clean_df().drop(columns=["temp_max_c"]), {}, DROPPED),
    ("renamed temp_max_c", clean_df().rename(columns={"temp_max_c": "tmax_c"}), {}, DROPPED),
    (
        "float32 retype",
        clean_df().astype({"temp_max_c": "float32"}),
        {},
        expect(schema__contract=ALARM),
    ),
    ("empty frame, columns kept", clean_df().iloc[0:0], {}, expect()),
]


def _kind(r: CheckResult) -> str:
    if r.status == "error":
        return "error"
    return ALARM if r.alarm else OK


@pytest.mark.parametrize(("name", "df", "kw", "want"), CASES, ids=[c[0] for c in CASES])
def test_gx_verdicts(name, df, kw, want):
    got = verdicts(ctx_for(df, **kw))
    assert sorted(got) == sorted(SUPPORTED_IDS)
    assert {k: _kind(r) for k, r in got.items()} == want, name
    for r in got.values():
        assert r.table == "raw.weather_daily"
        assert r.batch == 7


def test_frame_with_no_columns_errors_every_column_rule():
    got = verdicts(ctx_for(pd.DataFrame()))
    assert got["schema.contract"].alarm
    for cid in SUPPORTED_IDS:
        if cid != "schema.contract":
            assert got[cid].status == "error", cid


def test_unsupported_rules_are_not_run():
    got = verdicts(ctx_for(clean_df()))
    assert "build.stg_weather_daily" not in got  # SQL build
    assert "freshness.source_22" not in got  # needs the clock and the whole table
    assert not any(k.startswith(("volume", "freshness.lag", "null_rate")) for k in got)


def test_timestamp_without_zone_breaks_the_contract():
    df = clean_df()
    df["ingested_at"] = df["ingested_at"].dt.tz_localize(None)
    assert verdicts(ctx_for(df))["schema.contract"].alarm


def test_date_column_of_strings_breaks_the_contract():
    df = clean_df()
    df["date"] = df["date"].astype(str)
    assert verdicts(ctx_for(df))["schema.contract"].alarm


def test_large_loaded_history_stays_cheap():
    loaded = {(c, DAY - dt.timedelta(days=d)) for c in CITIES for d in range(1, 800)}
    base = ctx_for(clean_df())
    ctx = BatchContext(**{**base.__dict__, "loaded_keys": frozenset(loaded)})
    gx_parity.reset_gx_cost()
    got = verdicts(ctx)
    assert not got["unique.city_id_date"].alarm
    assert gx_parity.gx_cost()["seconds"] < 3.0


def test_cost_counters_track_batches():
    gx_parity.reset_gx_cost()
    verdicts(ctx_for(clean_df()))
    verdicts(ctx_for(hot()))
    cost = gx_parity.gx_cost()
    assert cost["batches"] == 2
    assert cost["validations"] > 2 * len(SUPPORTED_IDS)
    assert 0 < cost["seconds_per_batch"] < 5


def _r(check, status, batch=1):
    return CheckResult(
        check=check,
        family="nulls",
        table="t",
        batch=batch,
        status=status,
        observed=0.0,
        expected="",
    )


def test_parity_agrees_when_alarms_match_even_if_status_kinds_differ():
    own = [_r("a", "pass"), _r("b", "fail"), _r("c", "error")]
    gx = [_r("a", "pass"), _r("b", "error"), _r("c", "fail")]
    assert gx_parity.parity(own, gx) == []


def test_parity_reports_a_deliberate_mismatch():
    own = [_r("a", "pass"), _r("b", "fail", batch=2)]
    gx = [_r("a", "fail"), _r("b", "fail", batch=2), _r("z", "pass", batch=3)]
    msgs = gx_parity.parity(own, gx)
    assert len(msgs) == 2
    assert "a @ batch 1" in msgs[0] and "own pass" in msgs[0] and "GX fail" in msgs[0]
    assert "z @ batch 3" in msgs[1]


def test_parity_ignores_own_results_gx_does_not_cover():
    own = [_r("a", "pass"), _r("build.stg", "fail")]
    assert gx_parity.parity(own, [_r("a", "pass")]) == []
