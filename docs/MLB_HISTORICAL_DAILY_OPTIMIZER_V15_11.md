# MLB V15.11.1 Historical Daily-Slate Optimizer

## Status

This release implements the historical-data collection, signal search, walk-forward validation, untouched audit, and production-promotion controls for the MLB winner model.

It does **not** assert that a real 80–90% daily hit rate has already been achieved. The existing MLB authority remains active until real paid historical data passes every mandatory gate and an explicit write-once production cutover is executed.

Automatic wagering remains disabled.

## Development objective

The optimizer is required to:

1. Train on at least **1,000 settled MLB games**.
2. Select signal and weight combinations using at least **200 later games** across at least **14 whole validation dates**.
3. Open a strictly later, untouched audit of at least **200 games** across at least **14 whole dates** only after the candidate has been selected.
4. Reconstruct historical moneyline paths from **1:00 a.m. America/New_York** at exact **15-minute intervals**.
5. Freeze each game independently at **T-minus-45 minutes** before its scheduled first pitch.
6. Optimize the correctness of the **complete official daily slate**, not a confidence label assigned to an individual game.
7. Require exactly one winner pick for every official game. Missing, extra, or duplicate predictions fail the date.
8. Require at least **80% correct on every validation date and every untouched-audit date**.
9. Use **90%** as the stretch target and ranking objective, never as an unsupported label or guarantee.
10. Reject candidates that improve historical accuracy by leaking future information, omitting difficult games, degrading Brier score or log loss versus the market, or creating excessive train-to-validation or validation-to-audit divergence.

## Modules

### `hello_world/mlb_historical_policy_v1.py`

Defines the non-negotiable evidence contract, chronological whole-date partitioning, full-slate daily scoring, overfitting and calibration gates, authority resolution, and the fail-closed cutover record.

### `hello_world/mlb_historical_daily_optimizer_v1.py`

Provides:

- American-odds conversion and two-way de-vigging;
- bookmaker consensus, dispersion, sharp-book divergence, and overround signals;
- 1:00 a.m. ET / 15-minute historical request scheduling;
- per-game T-minus-45 clipping;
- opening, lock, multi-horizon movement, velocity, acceleration, volatility, path length, trend efficiency, reversal, favorite-flip, sharp-divergence, and liquidity features;
- regularized logistic base models;
- aggressive signal-weight perturbation, feature dropout, and market/model blending;
- lexicographic candidate ranking led by minimum daily-slate accuracy;
- fixed-candidate untouched audit evaluation.

### `scripts/mlb_historical_daily_optimizer_v15_11.py`

Provides explicit commands for:

- zero-call credit planning;
- resumable paid historical backfill;
- immutable request and response ledgers;
- T-minus-45 dataset construction;
- aggressive candidate search;
- one-time untouched audit;
- dry-run or executed production promotion.

### `.github/workflows/mlb-historical-optimizer-v15-11.yml`

A manual-only workflow that runs contract tests before any external action. It separates planning, paid backfill, dataset construction, training, audit, and promotion into explicit modes.

## Historical request controls

The `plan` command performs no paid request. It generates an immutable list of timestamps, estimates credits using the caller-supplied current credit rate, and hashes the plan.

The `backfill` command refuses to contact The Odds API unless all of the following are true:

- `ODDS_API_KEY` is present;
- the plan digest is valid;
- the exact authorization phrase is supplied;
- a positive hard credit ceiling is supplied;
- the estimated request cost is within the ceiling.

The command writes each response as a gzip-compressed immutable envelope and updates a resumable ledger after every successful response. Completed timestamps are not downloaded again. Observed provider request cost is tracked, and the operation stops if the explicit ceiling would be exceeded.

The authorization phrase is:

```text
I AUTHORIZE PAID THE ODDS API HISTORICAL USAGE
```

The request-cost default in the workflow is only a planning input. It must be checked against the provider's current billing rules before a paid run.

## Signal search

The search fits several regularized logistic base models and aggressively evaluates combinations of:

- signal weights;
- weight perturbation temperatures;
- feature dropout;
- model probability;
- de-vigged market probability;
- market/model blend.

The default search evaluates 25,000 combinations. The selected candidate is the lexicographic maximum of:

1. minimum validation-date hit rate;
2. fraction of validation dates reaching 80%;
3. mean validation-date hit rate;
4. lower Brier score;
5. lower log loss.

This ordering prevents a high average from hiding a failed day.

## Promotion gate

A candidate cannot be promoted unless the report proves all of the following:

- 1,000/200/200 game minimums;
- 14 validation and 14 audit date minimums;
- whole-date chronological partitions;
- audit opened only after selection;
- 1:00 a.m. ET collection start;
- exact 15-minute cadence;
- T-minus-45 clipping;
- settled official labels;
- no future feature leakage;
- 100% official-slate prediction coverage;
- no missing, extra, or duplicate game identities;
- every validation and audit date at or above 80%;
- bounded train-validation and validation-audit divergence;
- non-degrading validation and audit Brier score versus the market;
- non-degrading validation and audit log loss versus the market;
- an immutable artifact with a validated SHA-256 digest.

The production cutover phrase is:

```text
PROMOTE MLB V15.11.1 HISTORICAL DAILY OPTIMIZER ONLY
```

## Production authority transition

Passing the statistical gate alone does not alter production. Promotion also requires a separately explicit execution flag, DynamoDB target, AWS credentials, experiment identifier, and exact cutover phrase.

The executed transaction writes:

1. the checksum-bound approved champion record; and
2. a write-once `HISTORICAL_DAILY_OPTIMIZER_ONLY` cutover record.

After that cutover:

- legacy selection authority is false;
- legacy fallback is false;
- automatic legacy restoration is false;
- automatic wagering is false;
- a missing champion or artifact-digest mismatch resolves to `FAIL_CLOSED`, not V15.10.

The former implementation remains in source control for audit and disaster-recovery review, but it can no longer become the production selection authority automatically.

## Commands

Create a zero-call plan:

```bash
PYTHONPATH=hello_world:. python scripts/mlb_historical_daily_optimizer_v15_11.py plan \
  --schedule schedule.json \
  --credits-per-request 10 \
  --output request-plan.json
```

Execute an explicitly authorized backfill:

```bash
export ODDS_API_KEY="..."
PYTHONPATH=hello_world:. python scripts/mlb_historical_daily_optimizer_v15_11.py backfill \
  --plan request-plan.json \
  --output-dir historical-snapshots \
  --max-credits 75000 \
  --confirmation "I AUTHORIZE PAID THE ODDS API HISTORICAL USAGE"
```

Build the normalized dataset:

```bash
PYTHONPATH=hello_world:. python scripts/mlb_historical_daily_optimizer_v15_11.py build-dataset \
  --schedule schedule.json \
  --outcomes official-outcomes.json \
  --snapshot-dir historical-snapshots \
  --output mlb-dataset.jsonl
```

Search signal combinations:

```bash
PYTHONPATH=hello_world:. python scripts/mlb_historical_daily_optimizer_v15_11.py train \
  --dataset mlb-dataset.jsonl \
  --max-candidates 25000 \
  --seed 15111 \
  --output candidate-artifact.json
```

Open the untouched audit:

```bash
PYTHONPATH=hello_world:. python scripts/mlb_historical_daily_optimizer_v15_11.py audit \
  --dataset mlb-dataset.jsonl \
  --artifact candidate-artifact.json \
  --output gate-report.json
```

Evaluate promotion without writing production:

```bash
PYTHONPATH=hello_world:. python scripts/mlb_historical_daily_optimizer_v15_11.py promote \
  --report gate-report.json \
  --experiment-id mlb-v15.11.1-001 \
  --confirmation "PROMOTE MLB V15.11.1 HISTORICAL DAILY OPTIMIZER ONLY" \
  --output promotion-dry-run.json
```

Production execution additionally requires `--execute --table-name <table>`.

## What remains before production cutover

A credentialed run must still acquire and settle the real historical cohort, build the normalized dataset, search candidates, pass the chronological validation block, pass the strictly later untouched audit, validate the immutable artifact digest, and explicitly execute the write-once cutover.

Until that evidence exists, the system must report the release as **built but not promoted**.
