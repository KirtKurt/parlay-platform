# KS1 Phase 2 — LightGBM experiment

This PR trains and saves an experimental artifact. It does not merge, deploy,
register a serving model, change R8, or add a scheduler.

The PR-only workflow reads the existing `mlb/ks1/game-table-v1/` manifests,
verifies all parquet hashes, versions, row counts and date boundaries, then
trains on labeled dates before 2026-01-01 and tests on later labeled dates.
Features are selected solely from variable numeric Phase 1 `feature` columns
in the training period. Final labels, actual postgame starter identities,
game/team identifiers and audit fields cannot enter the classifier. Missing
values remain missing for LightGBM. Columns absent/constant in 2025 are omitted.
Market home probability is included, so this is a market-informed experiment.

Hyperparameters are fixed before evaluation: 200 trees, learning rate 0.03,
7 leaves, minimum 60 rows per leaf, L2 penalty 10, seed 1729, two threads,
deterministic column-wise training. There is no test-based tuning, early stopping,
test-based calibration, or final refit on the holdout.

The better-record baseline uses retained regular-season results with independent
completion timestamps. For every test game, only earlier Eastern dates and
results completed strictly before its cutoff count. Each team's win percentage
uses a Beta(1,1) prior; the higher percentage wins the comparison, ties pick home.
Its probability is home percentage divided by the sum of both percentages.
This is an explicitly heuristic probability, not fitted calibration. The source
is available archived results, not a claim of complete official standings.
Unknown completion times are excluded. Test-season earlier results may update
this sequential baseline, but never retrain the frozen LightGBM model.

The output includes the complete feature list, train/test dates and counts,
accuracy and Brier for both approaches on identical test games, ten reliability
buckets, per-game predictions, feature importances, parameters, source receipts,
and native `model.txt`. Reloaded model predictions must agree within 1e-12.

The existing model-artifact root, discovered in Phase 1, is
`s3://<MLB_ML_ARTIFACTS_BUCKET>/mlb/experiments/`; no local `models/` directory
exists. This job creates an isolated `ks1-phase2/<bundle-sha256>/` subdirectory
there and verifies every saved file by S3 readback. It does not write a champion,
registry or deployment pointer. Artifact storage is the only AWS write in this
phase, performed by the authorized GitHub PR job.

## Reproduce locally

Python 3.11+. Download the training run's artifact once and use its verified
input table and timestamped baseline ledger:

```bash
python -m pip install -r ks1/training-requirements.txt 'pytest>=8,<9'
python -m pytest -q tests/ks1_phase2
python -m ks1.train --input /path/to/input_table.parquet \
  --baseline /path/to/baseline_games.csv --output /tmp/ks1-phase2-local
```

With AWS read credentials, `python -m ks1.train --aws --output /tmp/ks1-phase2`
verifies and trains from deployed inputs. `--save-artifact` is restricted to
the repository PR training job. No provider archive or live API is downloaded.

Historical retrospective features can include subsequent scoring corrections.
Starter, park, weather and other process metrics absent from the training year
cannot be learned merely because some test-year values exist. Evaluation does
not imply a qualified or deployed model. Stop after metrics and the artifact.
