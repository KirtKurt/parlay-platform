# Parlay Platform

This repository contains the serverless parlay and prediction platform.

## Current production focus

The active MLB work is **MLB Predictive Platform V1**. It uses The Odds API MLB market data to collect 15-minute odds pulls, store line-movement history, and generate MLB game-winner predictions.

Start here:

- [`docs/MLB_PREDICTIVE_PLATFORM_V1.md`](docs/MLB_PREDICTIVE_PLATFORM_V1.md)
- scheduled handler: `hello_world/mlb_manual_pull.py`
- manual smoke endpoint: `POST /v1/pull/mlb`

## MLB V1 pipeline

Every HOT pull now performs the full record-and-score sequence:

1. Pull MLB odds from The Odds API using `baseball_mlb`.
2. Store date-isolated HOT snapshots.
3. Store canonical pull history under `PULLS#mlb#YYYY-MM-DD`.
4. Build movement features from 15-minute line deltas.
5. Store hot-side prediction rows.
6. Store all-game MLB winner predictions.

Scheduled pulls are gated by default until:

```text
MLB_PULL_START_AT_ET=2026-07-03T01:00:00-04:00
MLB_SCHED_INTERVAL_MINUTES=15
```

Manual validation can bypass the gate with `force=true`.

## Local / deployment notes

This is an AWS SAM application. The primary infrastructure definition is `template.yaml`.

Common commands:

```bash
sam build --use-container
sam deploy --guided
```

Required production secret:

```text
ODDS_API_KEY
```

Core tables are configured by SAM and exposed to the Lambda runtime as environment variables:

```text
SNAPSHOTS_TABLE
SIGNAL_LEDGER_TABLE
PREDICTIONS_TABLE
OUTCOMES_TABLE
```

## Smoke test after deployment

```bash
curl -X POST "$API_URL/v1/pull/mlb" \
  -H 'content-type: application/json' \
  -d '{"t":"HOT","run":"manual_v1_smoke","days_ahead":1,"force":true}'
```

Expected response fields:

```text
ok=true
platformVersion=MLB_PREDICTIVE_PLATFORM_V1
live_pull_ok=true
canonical_pull_history
game_winner_predictions
```
