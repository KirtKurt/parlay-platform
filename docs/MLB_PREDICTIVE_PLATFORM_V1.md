# MLB Single-Game Platform V2

## Installed goal

MLB V2 is the AWS production path for **individual MLB moneyline picks**. It uses The Odds API line movement and stored 15-minute pull history to generate a full single-game card, then locks that card 45 minutes before the first MLB game of the day.

This supersedes the older NFL/CFB parlay-first platform and the earlier MLB odds-only/prewired notes.

## Production components

The live scheduled pull handler is:

- `hello_world/mlb_manual_pull.py`
- SAM resource: `MLBAuditedPullFunction`
- manual endpoint: `POST /v1/pull/mlb`
- scheduled event: `MLBHotEvery15Min`

The single-game model is:

- `hello_world/mlb_game_winner_engine.py`
- model version: `INQSI-MLB-SINGLE-GAME-EV-PROMOTION-v2.0`
- ranking: EV + edge vs book + 15-minute movement + book agreement + guardrails

The primary MLB API surface is:

- `hello_world/inqsi_mlb_v1_core.py`
- `GET /v1/mlb/today`
- `GET /v1/mlb/game-winners`
- `GET /v1/mlb/predictions`
- `GET /v1/mlb/model/version`

The daily locked-card handler is:

- `hello_world/mlb_daily_pick_lock.py`
- SAM resource: `MLBDailyPickLockFunction`
- manual/status endpoints:
  - `POST /v1/mlb/locks/run`
  - `GET /v1/mlb/locks/status`
  - `GET /v1/mlb/locks/today`
- scheduled event: `MLBDailyPickLockEveryMinute`

## Data-source policy

MLB V2 uses **The Odds API only** for production odds and pick generation.

Required production source:

```text
ODDS_API_KEY
```

Not allowed for official picks:

```text
public sportsbook web pages
manual odds copied into the app
FanDuel/numberFire pages
NFL/CFB parlay files
local FastAPI demo slate
```

GitHub Actions must pass `secrets.ODDS_API_KEY` into SAM as the `OddsApiKey` parameter. The deployed Lambda must receive it as runtime env var `ODDS_API_KEY`.

## Cadence contract

Scheduled MLB odds pulls are HOT-only and same-day-only:

```text
MLBHotEvery15Min = cron(0/15 * * * ? *)
Input days_ahead = 0
MLB_SCHED_INTERVAL_MINUTES = 15
```

The lock runner checks every minute:

```text
MLBDailyPickLockEveryMinute = rate(1 minute)
MLB_DAILY_LOCK_MINUTES_BEFORE_FIRST_GAME = 45
```

The lock Lambda is read-only until the T-minus-45 window opens. It does not call The Odds API and does not burn additional odds quota.

## Storage contract

The platform writes to these DynamoDB key families:

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
record_type = mlb_daily_locked_individual_game_moneyline_picks
source = stored_odds_api_pull_history_latest_fresh_snapshot
lock_policy = first_mlb_game_minus_45_minutes
```

## Prediction contract

Every game produces one selected side. The selected side is not chosen by raw favorite probability. It is chosen by:

- real available book moneyline price,
- de-vigged book probability,
- consensus market probability across books,
- model probability blended from consensus and movement,
- expected value,
- edge versus the selected book price,
- pull depth,
- book agreement/divergence,
- underdog and favorite guardrails.

The prediction rows include:

```text
rank
gameId
gameKey
commenceTime
homeTeam
awayTeam
predictedWinner
predictedSide
book
americanOdds
marketSide
winProbabilityPct
marketProbabilityPct
edgeVsBookPct
expectedValuePct
score
confidenceTier
promotionStatus
promoted
pullCountForGame
guardrails
tags
```

## Promotion guardrails

Defaults:

```text
MLB_PROMOTION_THRESHOLD = 0.0015
MLB_MIN_PROMOTION_EV = 0.001
MLB_MIN_DOG_MODEL_PROB = 0.34
MLB_MAX_PROMOTED_DOG_PRICE = 160
MLB_HEAVY_FAVORITE_PRICE = -220
```

This lets viable underdogs promote without forcing every dog or burying all dogs under favorites.

## Lock guardrails

The official daily card will not lock unless:

- the T-minus-45 window has arrived, unless `force=true` is used for validation;
- stored Odds API pull history exists;
- the latest pull age is at or below `MLB_MAX_LOCK_SNAPSHOT_AGE_MINUTES`, default 20;
- all games are predicted when `MLB_REQUIRE_ALL_GAMES_FOR_LOCK=true`;
- each game has at least `MLB_MIN_PULLS_FOR_LOCK`, default 4, unless forced for validation.

## Parlay policy

MLB production is **individual game moneyline picks only**.

The legacy hot-side endpoint still exists for compatibility, but it now returns individual game rows and a disabled parlay payload:

```text
three_leg_parlay.disabled = true
parlaysEnabled = false
```

## Deployment policy

Use only:

```text
.github/workflows/deploy.yml
```

The duplicate odds-only deploy workflow was removed because it could race the same CloudFormation stack.

The deploy workflow runs:

```bash
python scripts/patch_template_mlb_v1.py
sam validate
sam build --no-cached
sam deploy
```

The SAM patch script enforces:

```text
quarter-hour MLB odds cron
same-day-only scheduled MLB pulls
single MLBAuditedPullFunction odds path
one-minute daily lock runner
admin token on lock/run writes
no duplicate MLB all-sports polling
```

## Validation checklist after deployment

Manual live odds-pull smoke test:

```bash
curl -X POST "$API_URL/v1/pull/mlb" \
  -H 'content-type: application/json' \
  -H "x-inqsi-admin-token: $INQSI_ADMIN_API_TOKEN" \
  -d '{"t":"HOT","run":"manual_v2_smoke","days_ahead":0,"force":true}'
```

Expected response:

```text
ok=true
platformVersion=MLB_PREDICTIVE_PLATFORM_V1 or newer
live_pull_ok=true
intervalMinutes=15
canonical_pull_history[0].ok=true
game_winner_predictions[0].ok=true
```

Read checks:

```bash
curl "$API_URL/v1/mlb/model/version"
curl "$API_URL/v1/mlb/today"
curl "$API_URL/v1/mlb/game-winners?store=false"
curl "$API_URL/v1/mlb/locks/status"
curl "$API_URL/v1/mlb/locks/today"
```

Lock proof:

```bash
curl -X POST "$API_URL/v1/mlb/locks/run" \
  -H 'content-type: application/json' \
  -H "x-inqsi-admin-token: $INQSI_ADMIN_API_TOKEN" \
  -d '{"force":true}'
```

Use `force=true` only for validation. Production locking should come from `MLBDailyPickLockEveryMinute` and should be immutable after the card is stored.

## Notes

- V2 does not claim guaranteed betting outcomes.
- Accuracy must be measured through settled results, closing-line value, hit rate by bucket, and ROI by threshold.
- If `live_pull_ok=false` or `fallback_used=true`, do not treat the output as a fresh official card.
- If the locked card is missing after T-minus-45, check `/v1/mlb/locks/status` for stale snapshot, shallow pull depth, incomplete card, or missing stored pull history.
