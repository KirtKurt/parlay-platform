# Parlay Platform

Production SAM application for the parlay syndicate platform.

## MLB V1 priority

MLB winner prediction is now **Odds API line-movement first**:

1. Pull Odds API `baseball_mlb` markets every 15 minutes.
2. Store every normalized pull into DynamoDB pull history.
3. Compare stored pulls for line movement, book agreement/divergence, reversals, run-line movement, and pull depth.
4. Predict the winner of each MLB game.
5. Store runtime proof and prediction rows for audit and later settlement.

The dedicated MLB recovery Lambda is `hello_world/mlb_hot_pull_recovery_lambda.py`. It runs through an `AWS::Scheduler::Schedule` created by `scripts/patch_template_mlb_hot_pull_recovery_permanent.py`:

- Start boundary: `2026-07-03T01:00:00-04:00` / `2026-07-03T05:00:00Z`
- Cadence: every 15 minutes
- Source: Odds API
- Sport key: `baseball_mlb`
- Markets: `h2h,spreads,totals`
- Storage path: `SNAPSHOTS_TABLE` pull history partition `PULLS#mlb#YYYY-MM-DD`
- Winner engine: `mlb_game_winner_engine.predict_all(store=True)`

SportsDataIO/fundamentals remain optional enrichments. They should not block the core MLB line-movement deployment.

## Main API routes

- `GET /v1/health`
- `GET /v1/mlb/today`
- `GET /v1/mlb/games`
- `GET /v1/mlb/predictions`
- `GET /v1/mlb/game-winners`
- `GET /v1/mlb/audit`
- `GET /v1/mlb/model/version`
- `GET /v1/sources/mlb/status`

## Required secrets

- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `AWS_REGION`
- `ODDS_API_KEY`

Optional:

- `SPORTSDATAIO_API_KEY`, `SPORTS_DATA_IO_API_KEY`, `SPORTSDATAIO_MLB_API_KEY`, or `SPORTS_DATA_IO_MLB_API_KEY`
- `INQSI_ADMIN_API_TOKEN`

## Deploy

Push to `main` or run the **Deploy SAM to AWS** workflow manually.

The workflow patches the SAM template, validates it, builds it, deploys it, and smoke-tests the MLB model/status/game-winner routes.
