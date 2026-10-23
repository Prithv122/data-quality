# Build Notes — data-quality

Working notes: what broke, what I tried, why X over Y.

---

## Log

### 2026-10-23 — contracts and pre-registration before any code

- **Scope change from the brief:** the brief put the #35 adapter "later". It is in scope now: the
  lake source reads `lake.events` through `lakehouse.contract` and must survive compaction.
- **Measured before writing the rule:** on #35 at `50bee72` (2,000 rows), a compaction leaves a
  DELETE snapshot with 0 records and then an APPEND restoring all 2,000, 99 ms apart. An empty
  overwrite (truncate) leaves DELETE 0 then APPEND 0. So "0 records" alone cannot tell a
  compaction from a truncate; the rule folds a 0-record snapshot into the next one when they are
  under 60 s apart, then compares rows with the previous version.
- **Second trap found while designing:** a compaction is a fresh commit, so freshness computed
  from the last snapshot's timestamp says the table is fresh when no new data has arrived for days.
  Freshness in the logical walk uses the last data-bearing version.
- **10 rows a day is too few to measure a null spike.** #22's real shape is 10 cities; a 5% null
  spike is half a row. The stream keeps #22's schema and constraints but runs 180 stations built
  from the 10 seed climates.
- **Static vs adaptive made fair:** both threshold arms use the same rule (median ± k · scaled
  MAD) and the same k; they differ only in which batches form the baseline. H1 compares them at a
  matched false-alarm rate (each arm's smallest k under 1 per 100 batch-checks), not at the same k.
- **Engine:** the brief recommended Great Expectations for the static checks. Own code evaluates
  every check instead (one code path for three arms, two sources and a k sweep of ~1M
  evaluations); GX 1.24.1 (Apache-2.0, checked on PyPI) re-implements the static rules and must
  agree on every batch of the smoke grid (gate G4). Soda Core stays out: Elastic License 2.0.
- **Caught in my own test of the threshold rule:** a rolling median lags a trend by about half its
  window times the slope. A climb of 2 per batch beat a k = 4, floor 1 band within three batches.
  The W1 growth (one station per four batches, ~0.25 rows per batch against a scale of ~4 rows) is
  far inside that.

### 2026-10-23 — components built in parallel, smoke grid green

- **Disclosure — smoke numbers seen before the reported run.** While building `simulate`, one
  builder ran a 1-seed W1 tryout with the real checks; I then ran the smoke grid through the CLI.
  Seen: at seed 3601, W1, both threshold arms reach the 1-per-100 target at k = 5 (k = 4 gave
  1.09 per 100 on the null-rate checks); H1 smoke a = 0, s = 0 over 2 conditions; silent shift 0 of
  3 for every arm. The pre-registration calls the smoke grid gates-only, nothing in `spec.py`,
  `checks.yml` or the hypotheses changed after it, and the reported run uses 5 seeds and both
  worlds.
- **Smoke gates:** G2 (every fault matches its label), G6, no attribution before onset, S0 silent
  on clean streams: all pass. 111,000 results in 18 s with 4 processes. G4 (`dq gx-parity`, seed
  3601, W1): 12,000 (check, batch) verdicts compared over 25 streams, 0 mismatches.
- **Bug caught by the CLI test, not the unit tests:** `dq run` on the clean fixture failed
  `schema.contract` with "column date: expected date, got timestamp". DuckDB returns `DATE` to
  pandas as `datetime64`. For a warehouse source the contract is now checked against the
  warehouse catalogue (information_schema), and the frame's dates are converted to the batch
  convention for the data checks.
- **GX behaviour that differed from dbt:** the column-pair expectation fails rows with a null by
  default; `ignore_row_if="either_value_is_missing"` matches dbt. GX cannot type-match pandas 3's
  `str` dtype through a type list, and checks object columns element by element. Its in-set
  expectation costs ~2 s a batch at 10k loaded keys, so the parity version only passes loaded keys
  sharing a date with the batch (same verdict: a key can only collide on an equal date).
- **Lake demo (5,000 rows):** the logical walk flags exactly the four faulted appends and nothing
  at the compaction; the naive walk raises volume alarms at both halves of the compaction and
  raises no volume alarm on the duplicate-id append (it counts every appended record as new,
  including the 50 that reuse ids; the logical walk counts only new ids).

---

## Rejected approaches

| Approach | Why rejected |
|---|---|
| Soda Core as the engine | Elastic License 2.0 since 2025: source-available, not open source |
| GX as the evaluating engine | Three arms × two sources × eight k values; GX per-batch overhead and API churn would dominate. Kept as an independent parity check instead |
| Static thresholds hand-picked per check | Makes "adaptive beats static" depend on my threshold choices; frozen-baseline k-MAD isolates the one difference |
| Treat every Iceberg snapshot as a version | Compaction reads as "emptied, then refilled" and resets freshness |

## Open questions

- [ ] Does the operating-point rule leave S1 able to reach 1 false alarm per 100 batch-checks in W1 at all?
- [ ] Real #22 run: Docker is available (29.7.2); #22's stack has to be brought up and loaded by its own pipeline.
