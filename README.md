# Parlay Platform

## Current production focus

The active production path is **MLB Predictive Platform V1.1**: MLB individual-game moneyline picks, powered by The Odds API, stored every 15 minutes, and locked once per slate 45 minutes before the first MLB game.

This repository still contains legacy NFL/CFB/parlay code, but the current MLB production workflow is:

```text
The Odds API -> MLB HOT pull every 15 minutes -> DynamoDB pull history -> single-game moneyline scoring -> daily locked MLB card
```

Start here:

- [`docs/MLB_PREDICTIVE_PLATFORM_V1.md`](docs/MLB_PREDICTIVE_PLATFORM_V1.md)
- scheduled pull handler: `hello_world/mlb_manual_pull.py`
- single-game winner engine: `hello_world/mlb_game_winner_engine.py`
- daily lock handler: `hello_world/mlb_daily_pick_lock.py`
- manual smoke endpoint: `POST /v1/pull/mlb`
- daily lock endpoints: `GET /v1/mlb/locks/status`, `GET /v1/mlb/locks/today`, `POST /v1/mlb/locks/run`

## MLB V1.1 pipeline

Every HOT pull performs the record-and-score sequence:

1. Pull MLB odds from The Odds API using `baseball_mlb`.
2. Store date-isolated HOT snapshots.
3. Store canonical pull history under `PULLS#mlb#YYYY-MM-DD`.
4. Build movement features from 15-minute line deltas.
5. Store hot-side prediction research rows.
6. Store individual MLB game-winner predictions.

The official locked-card scheduler runs every minute and writes one immutable item under:

```text
LOCKED_PICKS#mlb#YYYY-MM-DD
```

The card locks at:

```text
first MLB game start time - 45 minutes
```

## Production guardrails

MLB V1.1 fixes the prior local/AWS split by making the SAM/Lambda/DynamoDB path the production source of truth.

Key guardrails:

```text
MLBHotEvery15Min = cron(0/15 * * * ? *)
MLBDailyPickLockEveryMinute = rate(1 minute)
MLB_PRIMARY_BOOK = fanduel
MLB_PROMOTION_EDGE_THRESHOLD = 0.0015
MLB_MIN_PROMOTION_EV = 0.0
MLB_MAX_PROMOTED_DOG_PRICE = 170
MLB_MIN_PULLS_FOR_LOCK = 4
MLB_MAX_LOCK_SNAPSHOT_AGE_MINUTES = 20
```

The lock refuses to write the official card if the latest snapshot is stale, if the card is incomplete, or if games do not have enough pull depth.

## Local / deployment notes

This is an AWS SAM application. The primary infrastructure definition is `template.yaml`, with MLB production hardening applied by:

```text
scripts/patch_template_mlb_v1.py
scripts/verify_mlb_schedule_invariants.py
```

Common commands:

```bash
sam build --use-container
sam deploy --guided
```

The GitHub deploy workflow is the recommended deployment path because it verifies the Odds API secret, patches the SAM template, validates production invariants, deploys, and then performs a real live Odds API pull/storage smoke test.

Required production secret:

```text
ODDS_API_KEY
```

Core tables are configured by SAM and exposed to Lambda runtime as environment variables:

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
  -d '{"t":"HOT","run":"manual_v1_1_smoke","days_ahead":0,"force":true}'
```

Expected response fields:

```text
ok=true
platformVersion=MLB_PREDICTIVE_PLATFORM_V1
live_pull_ok=true
fallback_used=false
canonical_pull_history
game_winner_predictions
```

Then check:

```bash
curl "$API_URL/v1/mlb/game-winners?store=false"
curl "$API_URL/v1/mlb/locks/status"
curl "$API_URL/v1/mlb/locks/today"
```

## Legacy docs

The uploaded V15.0/V15.1 NFL/CFB guides describe the older parlay platform. They are not the MLB production runbook. For MLB operations, use `docs/MLB_PREDICTIVE_PLATFORM_V1.md`.
