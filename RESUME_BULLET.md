# Resume Bullets — data-quality

Form: **action → technical specifics → measured outcome.** Numbers or it doesn't go on the resume.
All numbers are from `results/REPORT.md` (synthetic 180-station stream, 5 seeds, commit `5f88111`,
all six correctness gates passed).

---

## Bullets

- Built a fault-injection harness that scores data quality checks against ground truth: 8 fault
  types × 3 severities × 5 seeds (120 labelled conditions) injected into a daily stream with my
  ELT warehouse's exact schema; the project's own dbt tests caught 70 of 120 and missed 10 of 24
  fault cells outright (every null spike, both volume drops), with zero false alarms.
- Compared frozen and rolling-baseline thresholds (median ± k·MAD) at a matched false-alarm budget
  (≤ 1 per 100 clean batch-checks): with a growing sensor network the frozen arm needed k = 8 and
  caught a 20% volume drop in 2 of 5 seeds versus 5 of 5 for the rolling arm at k = 5; on a
  stationary feed the two tied (105 / 120 each). Pre-registered hypotheses; verdicts computed by
  code and refused unless all gates pass on the same commit.
- Read an Apache Iceberg table through a sibling project's contract and handled the empty DELETE
  snapshot a compaction leaves: the logical walk stayed silent through compaction and flagged all
  four faulted appends, where a naive snapshot walk raised two false volume alarms; Great
  Expectations re-implemented the static rules and agreed on all 12,000 verdicts.

## Which roles this supports

- [x] Data Scientist / ML
- [ ] AI Engineer (LLM/NLP/CV)
- [x] Data Engineer
- [x] Data Analyst / Python Developer
