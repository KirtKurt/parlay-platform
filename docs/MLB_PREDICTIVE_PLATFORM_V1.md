# MLB Predictive Platform V1.1

## Installed production goal

MLB Predictive Platform V1.1 is the production path for **MLB individual-game moneyline picks**. It uses The Odds API MLB market data only, records every 15-minute pull into DynamoDB, scores single-game sides from stored pull history, and locks one immutable daily MLB card 45 minutes before the first MLB game of the slate.

This is not the NFL/CFB parlay app. MLB parlays are disabled in the primary MLB production surface.

## Production handlers

Live scheduled pull handler:

- `hello_world/mlb_manual_pull.py`
- SAM resource: `MLBAuditedPullFunction`
- manual endpoint: `POST /v1/pull/mlb`
- scheduled event: `MLBHotEvery15Min`
- schedule after the deploy patch: `cron(0/15 * * * ? *)`
- scheduled input: `days_ahead=0`

Game-winner / single-game API handler:

- `hello_world/inqsi_mlb_v1_core.py`
- SAM resource: `InqsiMLBV1CoreFunction`
- endpoints:
  - `GET /v1/mlb/today`
  - `GET /v1/mlb/games`
  - `GET /v1/mlb/predictions`
  - `GET /v1/mlb/game-winners`
  - `GET /v1/mlb/model/version`

Daily locked-card handler:

- `hello_world/mlb_daily_pick_lock.py`
- SAM resource: `MLBDailyPickLockFunction`
- endpoints:
  - `POST /v1/mlb/locks/run`
  - `GET /v1/mlb/locks/status`
  - `GET /v1/mlb/locks/today`
- scheduled event: `MLBDailyPickLockEveryMinute`

## Data-source policy

Production MLB odds and picks use **The Odds API only**.

Required GitHub/AWS secret:

```text
ODDS_API_KEY
```

GitHub Actions passes `secrets.ODDS_API_KEY` into the SAM deploy parameter `OddsApiKey`, which injects `ODDS_API_KEY` into the Lambda runtime. A GitHub secret existing by itself is not enough; deploy smoke now performs a real `POST /v1/pull/mlb` and fails if the deployed Lambda cannot use The Odds API successfully.

Removed or disabled from the MLB production path:

```text
SportsDataIO for MLB picks
public sportsbook page scraping
manual odds entry for official picks
NFL/CFB parlay scoring
MLB primary parlay output
legacy MLB T1/T2/T3/T4 schedules
all-sports duplicate MLB polling
```

## Pull cadence and slate contract

Scheduled MLB HOT pulls run on quarter-hour boundaries:

```text
cron(0/15 * * * ? *)
```

Scheduled pulls are same-day only:

```json
{"sport":"mlb","t":"HOT","run":"hot_pull_audited","days_ahead":0}
```

The start gate is evaluated in `America/New_York`:

```text
MLB_PULL_START_AT_ET=2026-07-02T01:00:00-04:00
MLB_SCHED_INTERVAL_MINUTES=15
```

Manual HTTP pulls and `force=true` validation can bypass the start gate.

## Storage contract

The pull handler writes to these DynamoDB key families:

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

Canonical pull history is the production source of truth for picks:

```text
PK = PULLS#mlb#YYYY-MM-DD
SK = PULL#<pulled_at>#<pull_id>
```

The lock item uses:

```text
PK = LOCKED_PICKS#mlb#YYYY-MM-DD
SK = DAILY_LOCK#TMINUS45
record_type = mlb_daily_locked_individual_game_picks
source = latest_stored_odds_api_pull_snapshot
lock_policy = first_mlb_game_minus_45_minutes
```

## Single-game prediction contract

`mlb_game_winner_engine.py` now produces individual game moneyline picks using:

- de-vigged market consensus across available books,
- provider event id first so MLB doubleheaders do not merge,
- 15-minute line movement and reversal count,
- book agreement/divergence,
- real bettable book price instead of synthetic consensus odds,
- EV and edge versus the selected book price,
- promotion guardrails for long dogs, heavy favorites, stale depth, and thin EV.

Default production env values:

```text
MLB_PRIMARY_BOOK=fanduel
MLB_PROMOTION_EDGE_THRESHOLD=0.0015
MLB_MIN_PROMOTION_EV=0.0
MLB_MAX_PROMOTED_DOG_PRICE=170
MLB_MIN_PULLS_FOR_LOCK=4
MLB_MAX_LOCK_SNAPSHOT_AGE_MINUTES=20
```

The response includes:

```text
predictedWinner
americanOdds
bookKey
priceSource
marketSide
winProbabilityPct
bookImpliedProbability
edgeVsBookPct
expectedValuePct
promotionStatus
promotionReasons
pullCountForGame
confidenceTier
tags
```

`promotionStatus` is one of:

```text
PROMOTED
WATCHLIST
NO_PLAY
```

## Daily lock contract

The daily lock scheduler runs every minute and locks once:

```text
first MLB game start time - 45 minutes
```

It does not call The Odds API directly. It locks only from stored Odds API pull history.

The lock will not write the official card unless:

- the latest pull contains the slate's current games,
- all games have a prediction when `MLB_REQUIRE_ALL_GAMES_FOR_LOCK=true`,
- the latest stored pull is no older than `MLB_MAX_LOCK_SNAPSHOT_AGE_MINUTES`,
- every locked game has at least `MLB_MIN_PULLS_FOR_LOCK` pulls,
- DynamoDB conditional put confirms the lock is immutable.

If guardrails fail, `/v1/mlb/locks/run` returns `LOCK_GUARDRAILS_NOT_MET` and does not write the lock unless `force=true` is used for validation.

## Deployment contract

Main deploy workflow:

```text
.github/workflows/deploy.yml
```

The workflow now:

1. Verifies the GitHub `ODDS_API_KEY` secret exists.
2. Patches the SAM template for MLB V1.1.
3. Runs production invariant validation.
4. Deploys the SAM stack.
5. Performs a real live Odds API smoke pull through the deployed API.
6. Fails if `live_pull_ok` is not true, if fallback/cached data is used, or if canonical pull history is not stored.

The older odds-only workflow is manual-only and shares the same deployment concurrency group to avoid racing the same CloudFormation stack.

## Validation commands after deployment

Live pull and storage proof:

```bash
curl -X POST "$API_URL/v1/pull/mlb" \
  -H 'content-type: application/json' \
  -d '{"t":"HOT","run":"manual_v1_1_smoke","days_ahead":0,"force":true}'
```

Expected:

```text
ok=true
platformVersion=MLB_PREDICTIVE_PLATFORM_V1
live_pull_ok=true
fallback_used=false
canonical_pull_history[0].ok=true
game_winner_predictions[0].ok=true
```

Read checks:

```bash
curl "$API_URL/v1/mlb/today"
curl "$API_URL/v1/mlb/game-winners?store=false"
curl "$API_URL/v1/mlb/locks/status"
curl "$API_URL/v1/mlb/locks/today"
```

Lock proof for validation only:

```bash
curl -X POST "$API_URL/v1/mlb/locks/run" \
  -H 'content-type: application/json' \
  -d '{"force":true}'
```

Use `force=true` only for validation. Production locking should come from `MLBDailyPickLockEveryMinute`, which locks automatically at T-minus-45 and refuses to overwrite the stored card.

## Notes

- V1.1 does not claim guaranteed betting outcomes. It creates transparent market-derived single-game picks and stores every pull needed to audit why each prediction was made.
- The deploy smoke test is intentionally live now; a deploy that cannot prove Odds API pull + storage should fail instead of silently serving stale picks.
- Static NFL/CFB parlay guides are legacy for MLB operations. This document is the production MLB runbook.
