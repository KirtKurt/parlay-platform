# KS1 Phase 6b — simulation candidate, PR only

This change retains the accepted Phase 2 LightGBM and Phase 3 Poisson artifacts
and their feature lists. It does not train, deploy, change R8, alter the T−10
boundary, add a vendor, or download a historical archive. Base: Phase 6 PR #703.

## Recipe and outputs

`KS1-terminal-poisson-no-tie-v1` draws 5,000 full-game **terminal score paths**
from the accepted home/away Poisson run inputs. Tied score pairs are redrawn
until each path has a winner. This is the independent Poisson distribution
conditional on a decisive final score, not a PA/inning simulator. The retained
archive does not provide an accepted PA transition model. No extra-inning or
automatic-runner rates are invented. Conditioning changes the expected runs
and win probability from the analytic model's half-tie convention.

Seeds bind game ID, recipe and accepted input-model identity. Run rates
parameterize the draws, not the seed; insignificant floating-point variation
cannot replace the complete Monte Carlo sample. Unchanged input rows reuse
the complete previous row, including simulation values and `as_of`. A changed
mapping invalidates only eligible unlocked rows. It never changes the raw
simulation seed. Each game records recipe, calibration version and path count.

The existing date prefix remains `mlb/ks1/predictions-v1/date=YYYY-MM-DD/`.

| Artifact / fields | Meaning |
| --- | --- |
| `predictions.parquet`: existing `p_home`, `lambda_home`, `lambda_away`, `proj_total` | Accepted champion outputs remain unchanged |
| `p_home_sim_raw` | Fraction of the same paths won by home |
| `p_home_sim` | Monotonic calibrated raw probability; identity initially |
| `lambda_home_sim`, `lambda_away_sim`, `proj_total_sim` | Empirical home, away and combined run means from those paths |
| `lambda_home_poisson`, `lambda_away_poisson`, `proj_total_poisson` | Explicit analytic comparison inputs; no backfill into frozen rows |
| `official_probability_field` | `p_home` for new rows; grading honors the field actually recorded at lock, including `p_home_sim` |
| `simulation.parquet` | Shadow view with `p_home_sim`, `lambda_home`, `lambda_away`, `proj_total` all derived from those paths |

All previously present fields on frozen rows are checked for equality. Even
legacy status-only migrations are skipped once frozen. Missing simulation
fields on old rows remain null; they are not reconstructed after the result.
The existing `preserve_frozen` function and cutoff inequalities are unchanged.
Conditional S3 date publication still rejects concurrent/stale prediction writes.

## Nightly lifecycle

The **existing** `mlb-research-ingestion.yml` keeps its hourly `:23` trigger.
The same workflow adds `0 5,6 * * *` plus an Eastern-hour gate to request nightly
01:00 America/New_York grading through DST. GitHub Actions schedules are best
effort; delays are not represented as an exact execution-time guarantee. The
nightly event does not run the 40-minute ingestion job. No AWS infrastructure,
Lambda lock schedule, ECS task, or second scheduling service is added.

The read-only capture enumerates versions of existing KS1 prediction objects.
A game is admitted only with actual S3 storage before T−10, a valid pregame
`as_of`, and an unchanged surviving row. A reconstructed historical `as_of`
alone is not lock evidence. Deleted, changed and unproven rows are reported.
Independent final runs and stable team IDs come from the existing full-box
`prior-games.json` artifact. Missing, tied or unfinished results stay pending.

Grades preserve the original official probability. They are keyed by game ID
in a new KS1 state artifact `mlb/ks1/simulation-v1/state.json` in the already
discovered artifacts bucket. State writes require the existing **main** workflow
and conditional ETag/create checks, followed by exact readback. PR capture mode
does not write AWS. Prediction objects are never touched by grading.

Every seven distinct graded simulation games triggers one calibration attempt.
A regularized logit mapping fits only older labels graded before the earliest
test snapshot. The seven later games compare the candidate with their actual
recorded simulation probabilities. Insufficient prior labels, reversed slopes,
tied Brier, or worse Brier retain the incumbent. Each decision stores train/test
IDs. Test games are never used to fit that candidate. Accepted mappings apply
only to subsequent unlocked predictions.

Every 50 distinct graded locked games creates an **optional, disabled** full-refit
request. No automatic full refit or model promotion is enabled. Only the initial
recipe is registered; an unknown recipe cannot replace it. `champion_gate`
requires paired games and strictly improved Brier, otherwise retains the old
recipe/mapping. No retrospective result promotes this initial shadow recipe.

## Runtime and verification

The default is 5,000 paths. A prior measured prediction slate above 240 seconds starts
subsequent work at 3,000; measured current-slate time also projects remaining
work and can reduce later games to 3,000. Daily timing includes model loading
and feature assembly; provider capture is separate. Path count is recorded on each row.
Unchanged/frozen rows remain cached. This is a downgrade guard, not a hard
four-minute timeout. No Fargate task was found in this repository; local and
GitHub timings must not be labeled Fargate measurements.

Local checks: **61 passed**. The retained five-game slate writes both Parquets;
unchanged replay is byte-identical, a synthetic scratch changes exactly one
game, and the next replay changes none. Tests also cover exact frozen-value
preservation across calibration changes, pre-cutoff storage evidence, immutable
official grading, chronological calibration, deduplication, 50-game requests,
regression rejection, DST and the 3,000-path fallback.

The accepted **retrospective, not locked** holdout remains 2,166 games,
2026-03-25–2026-09-09, with training 2025-04-01–2025-10-31:

| Model | n | Brier | Totals MAE |
| --- | ---: | ---: | ---: |
| LightGBM champion | 2,166 | 0.250463 | N/A — classifier has no run head |
| Poisson champion | 2,166 | 0.250961 | 3.601466 |
| Simulation candidate | 2,166 | 0.252367 | 3.607828 |

The candidate is worse here and remains shadow-only. Local replay took 3.85s
overall; the slowest historical slate took 0.038s. Read-only CI run [34442132009](https://github.com/KirtKurt/parlay-platform/actions/runs/34442132009)
found **zero existing KS1 prediction keys/versions and zero locked rows** at
2026-09-10T05:42:49Z. Consequently the locked comparison has n=0 and null Brier /
totals MAE for all three models. This requested prospective comparison is
blocked on actual retained KS1 locks; historical replay is not a substitute.
The initial read-only capture artifact
`ks1-phase6b-34442132009` (ID 10138321573) was downloaded and verified against
SHA-256 `00d5ba9ca627765f2997badc97ebf416e79cb08941c9811f0b43882368495571`.
The locked comparison is a separate artifact produced from storage evidence. All three models
use the same complete-game intersection; unavailable metrics remain null.

## Run locally

Python 3.11+; existing pinned `ks1/poisson-requirements.txt`.

```bash
python -m pip install -r ks1/poisson-requirements.txt pytest PyYAML
python -m pytest -q tests/ks1 tests/ks1_phase4 tests/ks1_phase5 tests/ks1_phase6b
python -m ks1.phase6b --phase2-dir /path/to/accepted-phase2 --output /tmp/ks1-sim-comparison
python -m ks1.verify_refresh --inputs /path/to/retained-capture --output /tmp/ks1-sim-refresh
python -m ks1.sim_nightly --inputs /path/to/readonly-capture.json --output /tmp/ks1-locked-grade
```

With the existing read permissions, `python -m ks1.sim_nightly --capture-only
--output /tmp/ks1-locked` inventories and compares real retained locks without
AWS writes or provider calls. The PR's existing Phase 3 comparison workflow
runs that capture plus the accepted retrospective replay and uploads receipts,
IDs, metrics and predictions. No deployment is requested.
