# MLB Predictive Platform V1

## Installed goal

MLB Predictive Platform V1 is the production path for using The Odds API line movement to pull, record, score, and predict MLB individual game winners every 15 minutes.

The live scheduled pull handler is:

- `hello_world/mlb_manual_pull.py`
- SAM resource: `MLBAuditedPullFunction`
- manual endpoint: `POST /v1/pull/mlb`
- scheduled event: `MLBHotEvery15Min`

The daily locked-card handler is:

- `hello_world/mlb_daily_pick_lock.py`
- SAM resource: `MLBDailyPickLockFunction`
- manual/status endpoints:
  - `POST /v1/mlb/locks/run`
  - `GET /v1/mlb/locks/status`
  - `GET /v1/mlb/locks/today`
- scheduled event: `MLBDailyPickLockEveryMinute`

## What was found in GitHub before the MLB V1 patches

1. `template.yaml` already had an MLB Lambda with a `rate(15 minutes)` schedule.
2. `odds_live_ingestion.py` already mapped app sport `mlb` to The Odds API sport key `baseball_mlb`.
3. `mlb_manual_pull.py` already stored HOT MLB snapshots under date-isolated DynamoDB partitions.
4. The game-winner engine, `mlb_game_winner_engine.py`, did not read those snapshot partitions. It reads canonical pull history from `inqsi_pull_history.query_pulls("mlb", slate_date)`, which expects `PULLS#mlb#YYYY-MM-DD` records.
5. That meant a scheduled MLB pull could record snapshots while the winner engine still had no canonical pull history to score.
6. The platform did not have an immutable daily lock card for all individual MLB games 45 minutes before the first game of the slate.

## V1 fix

`hello_world/mlb_manual_pull.py` now performs the full V1 pipeline on every HOT pull:

1. Pull MLB odds from The Odds API using `baseball_mlb`.
2. Store combined HOT snapshots under `SPORT#mlb`.
3. Store date-isolated HOT snapshots under `SPORT#mlb#DATE#YYYY-MM-DD`.
4. Store canonical line-movement pull history under `PULLS#mlb#YYYY-MM-DD`.
5. Store audit rows and no-edge prediction audit rows.
6. Build and store HOT movement feature rows under `ML_FEATURE#mlb#YYYY-MM-DD`.
7. Build and store date-isolated hot-side/game prediction rows through `mlb_date_signal_api.hot_sides(..., store=True)`.
8. Build and store all-game winner predictions through `mlb_game_winner_engine.predict_all(..., store=True)`.

`hello_world/mlb_daily_pick_lock.py` now performs the daily lock pipeline:

1. Runs every minute from AWS EventBridge.
2. Reads stored Odds API pull history only; it does not call The Odds API.
3. Determines the first MLB game start for the ET slate date.
4. Locks once the slate reaches `first_game_start_et - 45 minutes`.
5. Stores one immutable all-game individual moneyline pick card under `LOCKED_PICKS#mlb#YYYY-MM-DD`.
6. Uses a conditional DynamoDB put so repeated minute checks are idempotent and cannot overwrite the locked card.

## Time-zone and cadence contract

Scheduled, non-HTTP MLB odds pull events are gated by:

```text
MLB_PULL_START_AT_ET=2026-07-03T01:00:00-04:00
MLB_SCHED_INTERVAL_MINUTES=15
```

The SAM patch script changes `MLBHotEvery15Min` from `rate(15 minutes)` to quarter-hour cron:

```text
cron(0/15 * * * ? *)
```

That matters because a `rate(15 minutes)` EventBridge rule starts on the minute it is created, which can produce offsets such as `1:07`, `1:22`, `1:37`, and `1:52`. Quarter-hour cron always fires at `:00`, `:15`, `:30`, and `:45` UTC. Those minute boundaries are identical in America/New_York, and the Lambda start gate is evaluated in `ZoneInfo("America/New_York")`.

For July 3, 2026, New York is on daylight time, so the configured start gate:

```text
2026-07-03T01:00:00-04:00 America/New_York
```

is:

```text
2026-07-03T05:00:00Z UTC
```

Therefore the first eligible scheduled pull is the `05:00 UTC` quarter-hour invocation, equal to `1:00 AM ET`. Manual HTTP pulls and scheduled payloads with `force=true` bypass the gate for validation.

The daily lock scheduler is intentionally more frequent:

```text
MLBDailyPickLockEveryMinute = rate(1 minute)
MLB_DAILY_LOCK_MINUTES_BEFORE_FIRST_GAME = 45
```

The lock Lambda is cheap and read-only until the T-minus-45 window opens. It does not burn additional Odds API calls because odds ingestion is handled only by the 15-minute pull schedule.

## Data-source policy

MLB V1 does not use SportsDataIO for the production odds/picks path.

Required production data source:

```text
ODDS_API_KEY
```

Removed from the MLB V1 deploy path:

```text
SportsDataIO secret checks
SportsDataIO SAM template patching
SportsDataIO deploy parameter overrides
SportsDataIO smoke tests
```

GitHub Actions must pass `secrets.ODDS_API_KEY` into the SAM deploy parameter `OddsApiKey`, which injects `ODDS_API_KEY` into the deployed Lambda runtime.

## Storage contract

The V1 handler writes to the following DynamoDB key families:

```text
SPORT#mlb
SPORT#mlb#DATE#YYYY-MM-DD
PULLS#mlb#YYYY-MM-DD
ML_FEATURE#mlb#YYYY-MM-DD
PRED#mlb#YYYY-MM-DD
GAME_WINNERS#mlb#YYYY-MM-DD
LOCKED_PICKS#mlb#YYYY-MM-DD
AUDIT#mlb#YYYY-MM-DD
```

The lock item uses:

```text
PK = LOCKED_PICKS#mlb#YYYY-MM-DD
SK = DAILY_LOCK#TMINUS45
record_type = mlb_daily_locked_individual_game_picks
source = stored_odds_api_pull_history
lock_policy = first_mlb_game_minus_45_minutes
```

## Prediction contract

Game winner predictions are driven by:

- de-vigged moneyline consensus across available books,
- line movement from prior 15-minute pulls,
- book agreement/divergence,
- reversal count,
- pull depth,
- run-line confirmation where available.

The response now includes:

```text
canonical_pull_history
hot_movement_features
hot_side_predictions
game_winner_predictions
```

The locked card includes compact individual game picks:

```text
rank
gameId
commenceTime
homeTeam
awayTeam
predictedWinner
americanOdds
winProbabilityPct
score
confidenceTier
pullCountForGame
tags
```

## Validation checklist after deployment

Manual odds-pull smoke test:

```bash
curl -X POST "$API_URL/v1/pull/mlb" \
  -H 'content-type: application/json' \
  -d '{"t":"HOT","run":"manual_v1_smoke","days_ahead":0,"force":true}'
```

Expected response:

```text
ok=true
platformVersion=MLB_PREDICTIVE_PLATFORM_V1
live_pull_ok=true
intervalMinutes=15
canonical_pull_history[0].ok=true
game_winner_predictions[0].ok=true
```

Read checks:

```bash
curl "$API_URL/v1/inqsi/pulls/latest?sport=mlb"
curl "$API_URL/v1/inqsi/algorithm/signals?sport=mlb"
curl "$API_URL/v1/predictions/mlb/hot-sides?store=false&include_no_edge=true"
curl "$API_URL/v1/mlb/locks/status"
curl "$API_URL/v1/mlb/locks/today"
```

Lock proof:

```bash
curl -X POST "$API_URL/v1/mlb/locks/run" \
  -H 'content-type: application/json' \
  -d '{"force":true}'
```

Use `force=true` only for validation. Production locking should come from `MLBDailyPickLockEveryMinute`, which locks automatically at T-minus-45 and then refuses to overwrite the stored card.

## Notes

- V1 does not claim guaranteed betting outcomes. It produces transparent market-derived predictions and stores every pull needed to audit why each prediction was made.
- The Odds API key must be present as `ODDS_API_KEY` on the deployed Lambda.
- The schedule only becomes real in production after the SAM stack is deployed from this branch or the branch is merged and deployed by CI.
- Daily locks are immutable by design; if an early lock was produced through a forced validation run, delete the `LOCKED_PICKS#mlb#YYYY-MM-DD` item before production lock time if a clean production lock is required.
