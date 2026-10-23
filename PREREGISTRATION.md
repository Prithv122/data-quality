# Pre-registration — what data quality checks actually catch

Written 2026-10-23, before any fixture, fault, check or evaluation code exists. Everything it
refers to is committed with it: `src/dq/spec.py` (worlds, faults, seeds, arms, k grid, predicted
detections), `checks.yml` (the check definitions), `src/dq/types.py` and `src/dq/adaptive.py`
(the threshold rule both threshold arms share), and `tests/fixtures/contract_22.json` (the upstream
contract). Any later change is a dated amendment at the bottom, made before the run it governs.

## Question

With faults injected at known batches and severities into daily batches shaped like
`22-elt-dbt-dagster`'s `raw.weather_daily`, what detection recall (per fault type and severity)
and false-alarm rate per clean batch does each check family reach? And do thresholds whose
baseline rolls with recent batches beat thresholds frozen at calibration time, for volume and
null rate, at a matched false-alarm rate?

## Upstream contracts

- **#22 `22-elt-dbt-dagster` @ `e20cecc`**: tables, columns and logical types in
  `tests/fixtures/contract_22.json`; source freshness `warn_after 3 day / error_after 14 day` on
  `ingested_at`; the dbt tests listed under arm S0 below. The fixture warehouse (DuckDB) mirrors
  it: `raw` from a seeded generator (10 seed cities, 2023-01-01..2024-12-31), staging,
  intermediate and marts built by #22's own model SQL (vendored, MIT, header names repo and SHA).
- **#35 `35-lakehouse` @ `50bee72`**: `lakehouse.contract.open_table / snapshots / to_arrow`
  over `lake.events` (layout L1, ten equal appends, schema v1). `snapshots()` returns
  `(snapshot_id, committed_at UTC, total_records)` oldest first.
  **Empty snapshots after compaction.** #35's compaction is one overwrite commit that PyIceberg
  records as two snapshots: a DELETE with 0 records, then an APPEND with the rewritten rows.
  Measured today on #35 at 2,000 rows: the two are 99 ms apart, the APPEND restores the exact
  record count. An empty overwrite (truncate) is also two snapshots, DELETE 0 then APPEND 0.
  Walking `snapshots()` naively therefore sees a compaction as "table emptied, then refilled".
  The rule used here (below, "Lake source") folds such pairs into one logical version.

## Data — the simulated stream

The #22 fixture has 10 rows per day: a 5% null spike is half a row. The stream therefore keeps
#22's schema exactly but runs a **station network** derived from the 10 seed climates:
`STATIONS_PER_CITY` = 18 per city (180 stations, ids like `chennai-07`), each with a jittered
version of its city's climate. `raw.cities` in the stream holds one row per station.

- One batch per day, 60 batches from 2024-01-01. Batch *b* carries day *b*'s rows, lands at
  02:00 UTC the next day (`ingested_at`), checks run at 06:00 UTC.
- Natural noise: a station-day row is lost with p = 0.02 and arrives one batch late with
  p = 0.03; base null rates 0.5% (`temp_max_c`, `temp_min_c`), 2% (`precip_mm`), 1%
  (`wind_max_kmh`). Temperatures: seasonal sine + AR(1) noise (phi 0.7, sd 2.5), clamped to
  -50..54 °C so the largest silent shift stays inside #22's accepted range.
- **World W1 (primary):** the network grows; one station is onboarded every 4 batches
  (round-robin over cities), and its `raw.cities` row lands in the same batch. 180 → 195
  stations. **World W0 (sensitivity):** no growth. The growth is the only difference.
- Batches 1–14 are warm-up (baselines only, never scored). Batches 15–60 are scored.

## Faults — 8 types × 3 severities × 5 seeds = 120 conditions per world

Each condition is its own 60-batch stream: the clean stream of its seed with one fault active for
7 batches from an onset drawn uniformly in 15..54. Definitions (in `spec.FAULTS`):

| Fault | small | medium | large |
|---|---|---|---|
| schema_drift (`temp_max_c`) | retyped to float32 | renamed `tmax_c` | dropped |
| freshness_lapse | 1 batch missed | 3 missed | 7 missed (backlog arrives after) |
| volume | 20% of stations missing | 50% missing | previous batch replayed (×2) |
| null_spike (`temp_max_c` or `precip_mm`, drawn) | +5% of rows | +20% | +50% |
| duplicates (within batch) | 1 row | 1% of rows | 10% |
| out_of_range | 1 row min > max (in range) | 1 row `temp_max_c` = 200 | 10% of rows = 200 |
| orphan_fk (`city_id` unknown to `raw.cities`) | 1 row | 1% | 10% |
| silent_shift (all temperatures) | +1 °C | +3 °C | +5 °C |

Seeds 3601–3605. Each seed also has a **clean twin** (no fault) used for attribution and for
false alarms.

## Checks and arms

Check definitions are in `checks.yml`. Seven families: schema, freshness, volume, nulls, unique,
range, referential. Three arms:

- **S0 — #22's own tests:** `dbt build` of the staging model on the batch, source freshness
  (warn > 72 h, error > 336 h), `not_null` on `city_id` and `date`, `unique (city_id, date)`
  (within the batch and against keys already loaded), `accepted_range -90..60` on both
  temperatures, `temp_max_c - temp_min_c >= 0`, `relationships city_id → raw.cities`.
- **S1 — static thresholds:** S0 + schema contract equality + threshold checks
  (`volume.rows` two-sided, `null_rate.temp_max_c`, `null_rate.precip_mm`, `freshness.lag`,
  upper) with a **frozen** baseline: median and MAD of the warm-up batches.
- **A — adaptive thresholds:** the same as S1 but the baseline is the last 14 accepted batches
  (computed and not alarmed).

Threshold rule for both S1 and A: alarm when the metric leaves `median ± k · max(1.4826 · MAD,
floor)`; floors: volume 1% of the median, null rate 0.5 points, lag 1 hour. Default k = 4.
Alarm = status `warn`, `fail` or `error` (a check that crashes because a column is gone pages
someone too). `skipped` (e.g. null rate of an empty batch) is not an alarm.

Engine: own code evaluates every check (one code path for all arms and both sources). Great
Expectations Core 1.24.1 re-implements the static rules independently and must agree (gate G4).

## Metrics

- **Attributable alarm:** check *c* alarms at batch *b* inside the fault window and the same
  check does not alarm at *b* in the clean twin.
- **Detected:** at least one attributable alarm by the arm. **Recall** per (world, arm, fault,
  severity) = detected / 5 seeds, with Wilson 95% intervals. Also per family.
- **Time to detect:** first attributable alarm batch − onset (batches).
- **False alarms:** on the 5 clean streams, alarms per 100 batch-checks over scored batches
  (15–60), per arm, per check and per family. Also reported: alarms after a fault window ends in
  faulted streams that the clean twin does not have (baseline contamination).
- **k sweep:** S1 and A at every k in `K_GRID` = 2, 2.5, 3, 3.5, 4, 5, 6, 8.

## Hypotheses and decision rules

**H1 — rolling beats frozen at matched false alarms (world W1).** Operating point per arm: the
smallest k in `K_GRID` whose clean-stream false-alarm rate over `volume.rows`,
`null_rate.temp_max_c` and `null_rate.precip_mm` is ≤ 1.0 per 100 batch-checks (if none reaches
it, k = 8 and that is reported). On the 10 small-severity conditions (volume ×0.8, null +5%), count
conditions A detects and S1 misses (*a*) and the reverse (*s*). **Supported iff a > s.**
*Expectation:* supported in W1, where a frozen baseline drifts away from a growing network and
needs a larger k to stay quiet; in W0 a ≈ s (reported, does not decide H1).
*Null:* a ≤ s, i.e. adaptive ties or loses once false alarms are matched.

**H2 — the silent shift is a blind spot.** No arm detects `silent_shift` at any severity
(0 of 15 per arm per world). *Expected* and reported as a limit of rule-based checks; if any
check fires, the report names it and why.

**H3 — the predicted coverage matrix.** `spec.EXPECTED` lists, for arms S0 and A (S1 = A), the
families predicted to detect each (fault, severity) at k = 4 in W1, majority of seeds (≥ 3 of 5).
The report prints every cell where observation and prediction differ. No verdict; the number of
mismatches is reported as is.

## Lake source (#35) — descriptive, no hypothesis

Logical versions from `snapshots()`: a 0-record snapshot followed by another snapshot within 60 s
is the first half of an overwrite and is folded into it. A folded pair whose rows equal the
previous version's rows (same `event_id` multiset and row fingerprint) is **maintenance**
(compaction): table-level checks run on it, batch checks and baselines skip it, and it does not
refresh freshness. A folded pair with different rows is an **overwrite** (a truncate is an
overwrite to 0 rows). An unpaired 0-record snapshot is a real **empty** version. Batch checks run
on the rows each append adds; every result carries its `snapshot_id`.

Demo: #35's canonical `lake.events` (50,000 rows), compacted, then clean and faulted appends
(null `amount` on purchases, duplicate `event_id`, negative `amount`, half-size append). Reported
as a table, with no recall claim.

## Correctness gates — all must pass or the run is invalid

- **G1 contract.** The fixture's tables, columns and logical types equal `contract_22.json`;
  primary and foreign keys hold; #22's marts build.
- **G2 faults do what they say.** For every condition, the difference between the faulted stream
  and its clean twin is exactly the labelled fault (checked by an oracle in `faults.py` that
  shares no code with the checks).
- **G3 determinism.** Two runs of the same seeds produce identical results files.
- **G4 GX parity.** On the smoke grid (seed 3601, W1, all 24 conditions and the clean twin), Great
  Expectations and own code give the same verdict for every static rule GX expresses
  (`not_null`, `unique`, `accepted_range`, column pair, relationships, schema contract) on every
  batch.
- **G5 lake versions.** On a real #35 table: after a compaction the logical walk raises no volume
  or freshness alarm while the naive walk raises at least one; a truncate is flagged by both; a
  compaction after a stale period does not hide the staleness (logical walk). Time travel to the
  pre-compaction snapshot returns the same rows.
- **G6 attribution.** A clean stream scored against itself has zero attributable alarms.

An invalid run reports no verdict: the bug is fixed, the grid re-run, the failure logged in
`NOTES.md`. The hypotheses do not change.

## Scales

- **Smoke (CI):** seed 3601, world W1, k = 4 and the grid: 24 conditions + clean twin. Gates only.
- **Reported run (local):** 5 seeds × W1 and W0 × (24 conditions + clean twin) × K_GRID.
- **Real #22 (local, needs Docker):** the same static and schema checks over #22's Postgres after
  its own pipeline loads it; descriptive. If Docker is unavailable the README says it was not run.

## What would make me distrust the numbers

- Any clean-stream alarm from a static S0 check (the generator obeys #22's constraints, so S0
  should be silent on clean data; an alarm means a generator or check bug).
- A recall change between two runs of the same seed.
- An attributable alarm before a fault's onset (attribution must be zero there by construction).

## Limits stated up front

Synthetic data; the faults are the ones I chose, at severities I chose; 5 seeds per cell, so a
recall of 3/5 versus 5/5 is within noise. The station network is a scaled stand-in for #22's
10 cities. No distribution-shape checks are in scope, which is why H2 is expected. The lake demo
measures behaviour on one table shape, not detection power.
