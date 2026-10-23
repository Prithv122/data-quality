# Interview Prep — data-quality

**Five questions, five answers.** Numbers are from `results/REPORT.md` (synthetic stream, 5 seeds,
commit `5f88111`, all gates passed).

---

### Q1. Walk me through the architecture in 90 seconds.

**A:** A seeded generator produces a 60-batch daily stream with the exact schema of my ELT
project's `raw.weather_daily`: 180 weather stations, with realistic noise (lost rows, late rows,
base nulls). A fault injector takes that clean stream and produces 24 faulted copies (8 fault
types × 3 severities), each with the fault active for 7 batches from a random onset. An
independent oracle verifies each copy differs from the clean one by exactly the labelled fault.
Every batch of every stream goes through 14 checks in seven families. Static checks run once.
Threshold checks compute a metric (row count, null rate, freshness lag), which is judged by one
rule, median ± k·MAD, under a frozen and a rolling baseline at eight values of k. Evaluation
counts an alarm as a detection only if the clean twin doesn't raise it at the same batch. From
that come recall, false alarms on clean streams, time to detect, and mechanical verdicts for
hypotheses I wrote down first. Results go to SQLite, a static dashboard, and stdout/JSON/Slack
sinks. A second source reads my Iceberg project's table through its published contract.

### Q2. Why compare static and adaptive thresholds at a matched false-alarm rate instead of the same k?

**A:** Because at the same k the comparison mostly measures noise. Any rule that alarms more
will catch more. At k = 4 both arms caught 105 of 120 conditions in the growing world, but the
frozen arm raised 1.49 false alarms per 100 clean batch-checks and the rolling one 0.28. So I
fixed a budget first (1 per 100), picked each arm's smallest k that met it, and compared recall
there. The frozen arm needed k = 8, and at k = 8 it caught a 20% volume drop in 2 of 5 seeds.
The rolling arm needed only k = 5 and caught it in 5 of 5. Both arms also use the same rule and
floors, so the only difference is which batches form the baseline. Without that, "adaptive beats
static" would just measure whatever static thresholds I happened to pick.

### Q3. What's the weakest part, and what would break first at scale?

**A:** The weakest part is that the faults and severities are my choices on synthetic data, and
5 seeds per cell means 3/5 versus 5/5 is within noise. The checks also share a blind spot: a
silent +5 °C shift that stays in range was caught by no rule in any seed. At scale, the first
thing to break is that every check pulls the batch into pandas. The fix is to push the static
rules down as SQL that returns counts, turn the cross-batch uniqueness set into an anti-join on
the target table, and keep each metric's baseline window in a small state table. That replaces
recomputing it every batch.

### Q4. How do you know the numbers are right?

**A:** Six gates, and verdicts are refused unless all pass on the same clean commit:
- the fixture matches the upstream contract column for column;
- every injected fault matches its label, checked by an oracle that shares no code with the
  checks;
- two runs give identical results;
- Great Expectations, re-implementing the static rules independently, agreed on all 12,000
  verdicts of the smoke grid;
- the Iceberg reader stays silent through a compaction and flags a truncate;
- a clean stream scored against itself has zero attributable alarms.

Before the reported run, a fresh-context review found a real bug the gates had missed. Orphan
ids restarted at `unregistered-001` every batch, so a late row collided with yesterday's key and
the orphan fault also injected a duplicate. That would have put false "unique catches orphans"
rows into the coverage table. I fixed the injector and taught the oracle to look across batches.

### Q5. Your Iceberg reader: why can't you just walk the snapshots?

**A:** Because a compaction in PyIceberg is one overwrite commit that produces two snapshots: a
DELETE with 0 records, then an APPEND that restores every row. I measured them 99 ms apart.
Walked naively, that reads as "the table was emptied and refilled", which is two volume alarms.
The new commit also makes the table look fresh when no new data has arrived. But "0 records"
alone isn't enough to skip a snapshot, because a real truncate is DELETE 0 then APPEND 0. So the
reader folds a 0-record snapshot into the next one when they're under 60 s apart, then compares
rows with the previous version. Equal rows mean maintenance: table-level checks only, no batch,
no freshness refresh. Different rows mean an overwrite, and a truncate is flagged. On the
50,000-row demo the logical walk was silent through the compaction and flagged each faulted
append; the naive walk raised two false volume alarms.

---

## 30-second pitch

I measured what data quality checks actually catch. I injected 8 kinds of fault at 3 severities
into a stream shaped like my ELT project's warehouse, 120 labelled conditions, and scored three
rule sets against ground truth. The project's own dbt tests missed 10 of 24 fault cells
outright, including every null spike and both volume drops, with zero false alarms. Thresholds
with a rolling baseline beat frozen ones at a matched false-alarm budget, but only when the data
drifted; on a stationary feed they tied. And no rule caught an in-range temperature shift. All
the hypotheses were written down before any code.
