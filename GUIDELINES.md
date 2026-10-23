# data-quality — E6

**Tier:** 3 · **Category:** Data engineering · **Wave:** 4

The project's own engineering guide. Keep it short and current.

## What this is

A measurement of what data quality checks catch. Faults of eight types are injected at known
batches and three severities into a daily batch stream shaped like `22-elt-dbt-dagster`'s
`raw.weather_daily`. Every check family (schema, freshness, volume, nulls, unique, range,
referential) is scored on recall per fault and severity, false alarms per clean batch and time to
detect, for three arms: #22's own dbt tests, static thresholds and rolling (adaptive)
thresholds. A second source reads `35-lakehouse`'s Iceberg table through its contract, one
result per snapshot, with compaction's empty snapshots handled.

The experiment is fixed in `PREREGISTRATION.md`. `src/dq/spec.py`, `checks.yml`,
`src/dq/types.py`, `src/dq/adaptive.py` and `tests/fixtures/contract_22.json` are frozen with it;
changing any of them needs a dated amendment first.

## Stack

Python 3.13 · uv · DuckDB (fixture warehouse and the staging-model build check) · pandas ·
pyarrow · PyYAML. Optional groups: `gx` (Great Expectations Core, parity gate only), `lake`
(`lakehouse` from #35, pinned to its shipped commit), `postgres` (psycopg, real #22 run).

## Module contracts

| Module | Public API | Notes |
|---|---|---|
| `types.py` | `CheckResult`, `MetricValue`, `BatchContext`, `Status`, `Family`, `ALARM_STATUSES` | Frozen. Every engine and source emits `CheckResult` |
| `spec.py` | worlds, stream constants, `FAULTS`, `SEEDS`, `K_GRID`, `EXPECTED`, lake constants | Frozen |
| `adaptive.py` | `ThresholdRule`, `Decision`, `baseline_stats`, `judge`, `evaluate_series(metrics, rule)` | Frozen. Frozen vs rolling baseline; alarmed batches never enter the baseline |
| `checkdefs.py` | `load_checks(path) -> CheckSuite`, `CheckDef.threshold_rule(k, mode)`, `CheckSuite.for_arm(arm)` | Reads `checks.yml` |
| `contract.py` | `load_contract(path=None) -> Contract`, `logical_type(engine_type) -> str`, `pandas_logical_type(dtype) -> str`, `compare_columns(expected, actual) -> list[str]` | Logical types: text, double, real, integer, numeric, date, timestamptz, boolean |
| `fixture.py` | `seed_cities() -> DataFrame`, `generate_weather(cities, start, end, seed) -> DataFrame`, `build_fixture(path, seed=FIXTURE_SEED) -> Path` | DuckDB file with `raw`, `public_staging`, `public_intermediate`, `public_marts` |
| `sql22/` | #22's six model SQL files + `render(name) -> str` | Vendored from #22 @ `e20cecc` (MIT); `source()`/`ref()` rendered to schema-qualified names |
| `warehouse.py` | `Warehouse` protocol (`fetch_df(sql)`, `columns(table) -> dict[str, str]`, `close()`), `DuckDBWarehouse(path)`, `PostgresWarehouse(dsn)` | Postgres imported lazily (optional group) |
| `checks/` | `run_static(defn, ctx, contract=None) -> CheckResult`, `compute_metric(defn, ctx) -> MetricValue` | One module per family. Checks never assume the contract holds: a missing column is an `error` result, never an exception |
| `world.py` | `Stream`, `generate_stream(seed, world) -> Stream` | Station network, outages, late rows, base nulls, W1 growth |
| `faults.py` | `Condition`, `conditions(seeds, worlds) -> list[Condition]`, `inject(stream, cond) -> Stream`, `verify(clean, faulted, cond) -> list[str]` | `verify` is the G2 oracle; shares no code with `checks/` |
| `simulate.py` | `contexts(stream) -> list[BatchContext]`, `run_stream(stream, suite, ks) -> DataFrame`, `run_grid(seeds, worlds, ks) -> GridResult` | Long results table: one row per (batch, check, arm, k) |
| `evaluate.py` | `attributable(...)`, `recall_table`, `false_alarm_table`, `ttd_table`, `operating_points`, `h1_verdict`, `h2_verdict`, `expected_mismatches`, `wilson(k, n)` | Pure functions over the results table; verdicts mechanical |
| `report.py` | `render_markdown(evaluation) -> str`, `write_results(evaluation, out_dir)` | `results/REPORT.md` + CSVs |
| `lake.py` | `LogicalVersion`, `logical_versions(snaps, same_rows)`, `naive_versions(snaps)`, `run_lake_checks(table, walk, check_time, k)`, `build_demo(warehouse)` | Reads #35 only through `lakehouse.contract` |
| `gx_parity.py` | `gx_static_results(suite, ctx, contract) -> list[CheckResult]`, `parity(own, gx) -> list[str]` | G4. GX runs only here |
| `sinks.py` | `to_stdout`, `write_json`, `slack_payload`, `SlackTransport`, `send_slack` | Webhook URL from `DQ_SLACK_WEBHOOK_URL` only |
| `store.py` | SQLite: `init`, `write_run`, `write_summary`, `read_runs`, `read_results`, `read_summary` | Results database; never committed |
| `dashboard.py` | `build(store_path, out_dir) -> Path` | One static `index.html`, no JavaScript framework |
| `cli.py` | `dq contract / build-fixture / run / simulate / evaluate / dashboard / lake` | |

## Acceptance criteria

- [ ] Gates G1–G6 in the pre-registration pass on the smoke grid in CI and on the reported run.
- [ ] `dq simulate` + `dq evaluate` write `results/REPORT.md` with recall, false alarms, time to
      detect, verdicts and the predicted-versus-observed matrix, all computed by code.
- [ ] The lake source runs against #35's real table; compaction raises no alarm in the logical
      walk.
- [ ] `dq dashboard` builds a static site from the results store.
- [ ] Runs from a clean clone using only the documented steps; tests, lint and CI green.

## Conventions

- `uv` for dependencies, pinned exactly; `ruff check` + `ruff format`; `pytest`.
- Tests write warehouses and stores under pytest's `tmp_path`, never into the repo. DuckDB files,
  SQLite stores and raw results are never committed; `results/REPORT.md` and summary CSVs are.
- Local pytest on this machine: `--basetemp=<scratch dir>` (the sandbox blocks `%TEMP%`). Never put
  that in `pyproject.toml`.
- Tests marked `postgres` need #22's Docker stack and are deselected by default.
- No secrets. The Slack webhook URL is read from the environment only (`.env.example`).

## Project-specific notes

- The stream is a 180-station network built from #22's 10 seed cities: at 10 rows per batch a
  5% null spike is half a row. Same schema and constraints as #22; more rows per day.
- `unique.city_id_date` looks at the batch and at keys already loaded: #22's primary key would
  reject a replayed batch at load time, and this check reports what that load would hit.
- #35's compaction writes two snapshots (DELETE with 0 records, then APPEND). `lake.py` folds them
  into one maintenance version; anything that walks `snapshots()` naively sees "table emptied,
  then refilled" and a fresh commit that is not fresh data.
- A rolling median lags a trend by about half its window times the slope. The W1 growth
  (one station per 4 batches) is far below what the scale floor tolerates.
