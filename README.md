# INQSI MLB Single-Game Platform

This repository is now the **production source of truth for MLB individual-game moneyline picks** on AWS.

The active AWS runtime is **SAM + Lambda + DynamoDB**, not the older local FastAPI/Terraform parlay scaffold. The old NFL/CFB parlay docs and files are legacy reference only and must not be used for MLB production picks.

## Current production focus

**MLB Single-Game Platform V2**

- Sport: **MLB only** for this production path.
- Pick type: **individual game moneyline picks only**.
- No MLB parlays on the primary production surface.
- Odds source: **The Odds API only** using sport key `baseball_mlb`.
- Storage: DynamoDB stored pull history under `PULLS#mlb#YYYY-MM-DD` plus date-isolated snapshots.
- Schedule: every 15 minutes, same-day slate only.
- Lock: one immutable daily card **45 minutes before the first MLB game**.
- Ranking: EV + edge vs real book price + line movement + guardrails, not raw favorite win probability.

## Production handlers

| Purpose | Lambda handler | Route / schedule |
|---|---|---|
| Odds ingest | `hello_world/mlb_manual_pull.py` | `POST /v1/pull/mlb`, `MLBHotEvery15Min` |
| Single-game picks | `hello_world/inqsi_mlb_v1_core.py` | `/v1/mlb/today`, `/v1/mlb/game-winners`, `/v1/mlb/predictions` |
| EV promotion model | `hello_world/mlb_game_winner_engine.py` | imported by ingest, picks, and lock handlers |
| Daily card lock | `hello_world/mlb_daily_pick_lock.py` | `POST /v1/mlb/locks/run`, `GET /v1/mlb/locks/today`, `MLBDailyPickLockEveryMinute` |
| Legacy compatibility | `hello_world/mlb_date_signal_api.py` | returns single-game rows; parlay payload is disabled |

## What was removed or disabled

- Duplicate odds-only deploy workflow: `.github/workflows/deploy-odds-only.yml` was removed.
- MLB parlay output is disabled on the primary MLB API surface.
- The generic all-sports scheduler must not pull MLB.
- Legacy MLB `T1/T2/T3/T4` schedules are removed by the SAM patch.
- Scheduled MLB pulls use same-day `days_ahead:0`, not tomorrow/two-day mixed slates.

## Required GitHub / AWS secrets

GitHub Actions must pass the secret into SAM deploy:

```text
ODDS_API_KEY
INQSI_ADMIN_API_TOKEN
AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY
AWS_REGION
```

`ODDS_API_KEY` must arrive in the Lambda runtime as `ODDS_API_KEY`. GitHub Secrets alone are not enough; the SAM deploy must pass it as the `OddsApiKey` parameter.

## Deploy path

Use the single main workflow:

```text
.github/workflows/deploy.yml
```

That workflow runs:

```bash
python scripts/patch_template_mlb_v1.py
sam validate
sam build --no-cached
sam deploy --parameter-overrides OddsApiKey="$ODDS_API_KEY_VALUE" InqsiAdminApiToken="$INQSI_ADMIN_API_TOKEN_VALUE"
```

The patch script canonicalizes the deploy template by enforcing:

```text
MLBHotEvery15Min = cron(0/15 * * * ? *)
MLBDailyPickLockEveryMinute = rate(1 minute)
MLB_MIN_PULLS_FOR_LOCK = 4
MLB_MAX_LOCK_SNAPSHOT_AGE_MINUTES = 20
INQSI_ADMIN_API_TOKEN on lock/run write endpoint
```

## Runtime checks

After deploy, check these endpoints:

```bash
curl "$API_URL/v1/mlb/model/version"
curl "$API_URL/v1/mlb/today"
curl "$API_URL/v1/mlb/game-winners?store=false"
curl "$API_URL/v1/mlb/locks/status"
```

Manual live Odds API smoke test:

```bash
curl -X POST "$API_URL/v1/pull/mlb" \
  -H 'content-type: application/json' \
  -H "x-inqsi-admin-token: $INQSI_ADMIN_API_TOKEN" \
  -d '{"t":"HOT","run":"manual_v2_smoke","days_ahead":0,"force":true}'
```

Expected live-pull result:

```text
ok=true
live_pull_ok=true
canonical_pull_history[0].ok=true
game_winner_predictions[0].ok=true
```

Daily lock check:

```bash
curl "$API_URL/v1/mlb/locks/status"
curl "$API_URL/v1/mlb/locks/today"
```

Expected at/after lock time:

```text
locked=true
source=stored_odds_api_pull_history_latest_fresh_snapshot
promotion_count >= 0
picks include edgeVsBookPct, expectedValuePct, marketSide, book, and promotionStatus
```

## Guardrails

The official card will not lock unless:

- the lock window has arrived, unless `force=true` is used for validation;
- stored Odds API pull history exists for the slate date;
- the latest pull is fresh enough, default max age 20 minutes;
- all games are predicted when `MLB_REQUIRE_ALL_GAMES_FOR_LOCK=true`;
- each game has at least 4 stored 15-minute pulls, unless `force=true` is used.

## Legacy note

The old uploaded V15.0/V15.1 guides described NFL/CFB parlay-first behavior and MLB as odds-only/prewired. Those guides are superseded for MLB production. This repo's active MLB path is AWS SAM/Lambda/DynamoDB with individual-game moneyline picks only.
