# Data quality checks: what they catch

## Run

- **seeds**: 3601, 3602, 3603, 3604, 3605
- **worlds**: W1, W0
- **ks**: 2, 2.5, 3, 3.5, 4, 5, 6, 8
- **git_sha**: 5f88111
- **scale**: full
- **results_sha256**: 7760fb02dd5bf1d21147c07a6ef32e48eac37a63559dff94d9dc20a3ddc461b9
- **report_rendered_at**: 4fed225
- **stream**: 60 batches, warm-up 14, fault window 7 batches, default k 4

## Verdicts

- **H1**, world W1: **supported**. a = 4 (A detects, S1 misses), s = 1 (S1 detects, A misses) over 10 small-severity conditions; k_A = 5, k_S1 = 8; clean FA/100: A 0.29, S1 0.14 (target 1/100 reached by both)
- **H1**, world W0: **reported**. a = 0 (A detects, S1 misses), s = 0 (S1 detects, A misses) over 10 small-severity conditions; k_A = 4, k_S1 = 5; clean FA/100: A 0.72, S1 0.87 (target 1/100 reached by both); sensitivity world, does not decide H1
- **H2**, world W1: **supported**. detected / conditions at k = 4: S0 0/15, S1 0/15, A 0/15
- **H2**, world W0: **supported**. detected / conditions at k = 4: S0 0/15, S1 0/15, A 0/15

### Operating points

| World | Arm | k | Clean FA per 100 | Target reached |
|---|---|---|---|---|
| W1 | S1 | 8 | 0.14 | yes |
| W1 | A | 5 | 0.29 | yes |
| W0 | S1 | 5 | 0.87 | yes |
| W0 | A | 4 | 0.72 | yes |

## Recall

### Recall, world W1

Detected conditions / conditions, Wilson 95% interval. Operating point: the smallest k whose clean false-alarm rate over the volume and null-rate checks meets the target.

| Fault | Severity | S0 | S1 k=4 | A k=4 | S1 at operating point k=8 | A at operating point k=5 |
|---|---|---|---|---|---|---|
| schema_drift | small | 0/5 (0.00–0.43) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) |
| schema_drift | medium | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) |
| schema_drift | large | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) |
| freshness_lapse | small | 0/5 (0.00–0.43) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) |
| freshness_lapse | medium | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) |
| freshness_lapse | large | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) |
| volume | small | 0/5 (0.00–0.43) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 2/5 (0.12–0.77) | 5/5 (0.57–1.00) |
| volume | medium | 0/5 (0.00–0.43) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) |
| volume | large | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) |
| null_spike | small | 0/5 (0.00–0.43) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 4/5 (0.38–0.96) | 4/5 (0.38–0.96) |
| null_spike | medium | 0/5 (0.00–0.43) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) |
| null_spike | large | 0/5 (0.00–0.43) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) |
| duplicates | small | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) |
| duplicates | medium | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) |
| duplicates | large | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) |
| out_of_range | small | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) |
| out_of_range | medium | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) |
| out_of_range | large | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) |
| orphan_fk | small | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) |
| orphan_fk | medium | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) |
| orphan_fk | large | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) |
| silent_shift | small | 0/5 (0.00–0.43) | 0/5 (0.00–0.43) | 0/5 (0.00–0.43) | 0/5 (0.00–0.43) | 0/5 (0.00–0.43) |
| silent_shift | medium | 0/5 (0.00–0.43) | 0/5 (0.00–0.43) | 0/5 (0.00–0.43) | 0/5 (0.00–0.43) | 0/5 (0.00–0.43) |
| silent_shift | large | 0/5 (0.00–0.43) | 0/5 (0.00–0.43) | 0/5 (0.00–0.43) | 0/5 (0.00–0.43) | 0/5 (0.00–0.43) |

### Recall, world W0

Detected conditions / conditions, Wilson 95% interval. Operating point: the smallest k whose clean false-alarm rate over the volume and null-rate checks meets the target.

| Fault | Severity | S0 | S1 k=4 | A k=4 | S1 at operating point k=5 | A at operating point k=4 |
|---|---|---|---|---|---|---|
| schema_drift | small | 0/5 (0.00–0.43) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) |
| schema_drift | medium | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) |
| schema_drift | large | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) |
| freshness_lapse | small | 0/5 (0.00–0.43) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) |
| freshness_lapse | medium | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) |
| freshness_lapse | large | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) |
| volume | small | 0/5 (0.00–0.43) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) |
| volume | medium | 0/5 (0.00–0.43) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) |
| volume | large | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) |
| null_spike | small | 0/5 (0.00–0.43) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) |
| null_spike | medium | 0/5 (0.00–0.43) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) |
| null_spike | large | 0/5 (0.00–0.43) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) |
| duplicates | small | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) |
| duplicates | medium | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) |
| duplicates | large | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) |
| out_of_range | small | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) |
| out_of_range | medium | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) |
| out_of_range | large | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) |
| orphan_fk | small | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) |
| orphan_fk | medium | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) |
| orphan_fk | large | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) | 5/5 (0.57–1.00) |
| silent_shift | small | 0/5 (0.00–0.43) | 0/5 (0.00–0.43) | 0/5 (0.00–0.43) | 0/5 (0.00–0.43) | 0/5 (0.00–0.43) |
| silent_shift | medium | 0/5 (0.00–0.43) | 0/5 (0.00–0.43) | 0/5 (0.00–0.43) | 0/5 (0.00–0.43) | 0/5 (0.00–0.43) |
| silent_shift | large | 0/5 (0.00–0.43) | 0/5 (0.00–0.43) | 0/5 (0.00–0.43) | 0/5 (0.00–0.43) | 0/5 (0.00–0.43) |

## False alarms

### False alarms on clean streams, world W1

Alarms / batch-checks (alarms per 100 batch-checks), scored batches 15–60.

| Arm | schema | freshness | volume | nulls | unique | range | referential | all |
|---|---|---|---|---|---|---|---|---|
| S0 | 0/230 (0.00) | 0/230 (0.00) | n/a | 0/460 (0.00) | 0/230 (0.00) | 0/690 (0.00) | 0/230 (0.00) | 0/2070 (0.00) |
| S1 k=4 | 0/460 (0.00) | 0/460 (0.00) | 34/230 (14.78) | 14/920 (1.52) | 0/230 (0.00) | 0/690 (0.00) | 0/230 (0.00) | 48/3220 (1.49) |
| A k=4 | 0/460 (0.00) | 0/460 (0.00) | 2/230 (0.87) | 7/920 (0.76) | 0/230 (0.00) | 0/690 (0.00) | 0/230 (0.00) | 9/3220 (0.28) |
| S1 at operating point k=8 | 0/460 (0.00) | 0/460 (0.00) | 1/230 (0.43) | 0/920 (0.00) | 0/230 (0.00) | 0/690 (0.00) | 0/230 (0.00) | 1/3220 (0.03) |
| A at operating point k=5 | 0/460 (0.00) | 0/460 (0.00) | 1/230 (0.43) | 1/920 (0.11) | 0/230 (0.00) | 0/690 (0.00) | 0/230 (0.00) | 2/3220 (0.06) |

### False alarms on clean streams, world W0

Alarms / batch-checks (alarms per 100 batch-checks), scored batches 15–60.

| Arm | schema | freshness | volume | nulls | unique | range | referential | all |
|---|---|---|---|---|---|---|---|---|
| S0 | 0/230 (0.00) | 0/230 (0.00) | n/a | 0/460 (0.00) | 0/230 (0.00) | 0/690 (0.00) | 0/230 (0.00) | 0/2070 (0.00) |
| S1 k=4 | 0/460 (0.00) | 0/460 (0.00) | 0/230 (0.00) | 14/920 (1.52) | 0/230 (0.00) | 0/690 (0.00) | 0/230 (0.00) | 14/3220 (0.43) |
| A k=4 | 0/460 (0.00) | 0/460 (0.00) | 0/230 (0.00) | 5/920 (0.54) | 0/230 (0.00) | 0/690 (0.00) | 0/230 (0.00) | 5/3220 (0.16) |
| S1 at operating point k=5 | 0/460 (0.00) | 0/460 (0.00) | 0/230 (0.00) | 6/920 (0.65) | 0/230 (0.00) | 0/690 (0.00) | 0/230 (0.00) | 6/3220 (0.19) |
| A at operating point k=4 | 0/460 (0.00) | 0/460 (0.00) | 0/230 (0.00) | 5/920 (0.54) | 0/230 (0.00) | 0/690 (0.00) | 0/230 (0.00) | 5/3220 (0.16) |

## Time to detect

### Time to detect, world W1

Batches from onset to the first attributable alarm: median (max), detected n.

| Fault | Severity | S0 | S1 k=4 | A k=4 | S1 at operating point k=8 | A at operating point k=5 |
|---|---|---|---|---|---|---|
| schema_drift | small | – | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 |
| schema_drift | medium | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 |
| schema_drift | large | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 |
| freshness_lapse | small | – | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 |
| freshness_lapse | medium | 2 (max 2), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 |
| freshness_lapse | large | 2 (max 2), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 |
| volume | small | – | 0 (max 1), n=5 | 0 (max 0), n=5 | 0 (max 0), n=2 | 0 (max 0), n=5 |
| volume | medium | – | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 |
| volume | large | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 |
| null_spike | small | – | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=4 | 0 (max 0), n=4 |
| null_spike | medium | – | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 |
| null_spike | large | – | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 |
| duplicates | small | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 |
| duplicates | medium | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 |
| duplicates | large | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 |
| out_of_range | small | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 |
| out_of_range | medium | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 |
| out_of_range | large | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 |
| orphan_fk | small | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 |
| orphan_fk | medium | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 |
| orphan_fk | large | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 |
| silent_shift | small | – | – | – | – | – |
| silent_shift | medium | – | – | – | – | – |
| silent_shift | large | – | – | – | – | – |

### Time to detect, world W0

Batches from onset to the first attributable alarm: median (max), detected n.

| Fault | Severity | S0 | S1 k=4 | A k=4 | S1 at operating point k=5 | A at operating point k=4 |
|---|---|---|---|---|---|---|
| schema_drift | small | – | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 |
| schema_drift | medium | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 |
| schema_drift | large | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 |
| freshness_lapse | small | – | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 |
| freshness_lapse | medium | 2 (max 2), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 |
| freshness_lapse | large | 2 (max 2), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 |
| volume | small | – | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 |
| volume | medium | – | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 |
| volume | large | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 |
| null_spike | small | – | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 |
| null_spike | medium | – | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 |
| null_spike | large | – | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 |
| duplicates | small | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 |
| duplicates | medium | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 |
| duplicates | large | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 |
| out_of_range | small | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 |
| out_of_range | medium | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 |
| out_of_range | large | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 |
| orphan_fk | small | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 |
| orphan_fk | medium | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 |
| orphan_fk | large | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 | 0 (max 0), n=5 |
| silent_shift | small | – | – | – | – | – |
| silent_shift | medium | – | – | – | – | – |
| silent_shift | large | – | – | – | – | – |

## Predicted versus observed families (world W1)

1 (arm, fault, severity, family) cells differ.

| Arm | Fault | Severity | Family | Predicted | Seeds detected |
|---|---|---|---|---|---|
| S1 | volume | medium | nulls | silent | 3/5 |

## Alarms after the fault window

Alarms in a faulted stream after its window closed that the clean twin lacks. Two causes are not baseline contamination: the 7-batch freshness lapse delivers its backlog one batch after the window (a real volume spike), and S1's frozen baseline cannot be contaminated at all, so its rows here are direct after-effects. Only A's rows can come from a fault entering the rolling baseline.

| World | Arm | k | Fault | Severity | Alarms |
|---|---|---|---|---|---|
| W1 | S1 | 4 | freshness_lapse | large | 5 |
| W1 | A | 4 | freshness_lapse | small | 1 |
| W1 | A | 4 | freshness_lapse | medium | 3 |
| W1 | A | 4 | freshness_lapse | large | 7 |
| W1 | A | 4 | duplicates | medium | 1 |
| W1 | A | 4 | duplicates | large | 2 |
| W1 | S1 | 8 | freshness_lapse | large | 5 |
| W1 | A | 5 | freshness_lapse | medium | 1 |
| W1 | A | 5 | freshness_lapse | large | 5 |
| W1 | A | 5 | duplicates | medium | 1 |
| W1 | A | 5 | duplicates | large | 1 |
| W0 | S1 | 4 | freshness_lapse | large | 5 |
| W0 | A | 4 | freshness_lapse | small | 1 |
| W0 | A | 4 | freshness_lapse | large | 5 |
| W0 | A | 4 | volume | small | 2 |
| W0 | A | 4 | volume | medium | 2 |
| W0 | A | 4 | duplicates | large | 2 |
| W0 | S1 | 5 | freshness_lapse | large | 5 |
