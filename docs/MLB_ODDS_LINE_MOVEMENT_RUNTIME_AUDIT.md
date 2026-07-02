# MLB Odds API Line-Movement Runtime Audit

## Decision

MLB V1 winner prediction is Odds API line-movement first. Optional fundamentals providers can enrich later, but they must not block the core pull-store-predict path.

## Required runtime contract

Every 15-minute MLB run must do all of the following:

1. Pull Odds API `baseball_mlb` lines.
2. Normalize `h2h`, `spreads`, and `totals` markets.
3. Store the pull into DynamoDB under `PULLS#mlb#YYYY-MM-DD`.
4. Build winner predictions with `mlb_game_winner_engine.predict_all(store=True)`.
5. Store a runtime proof report under `MLB_HOT_PULL_RECOVERY#LATEST` and `MLB_HOT_PULL_RECOVERY#RUNS`.

## Scheduler contract

The dedicated winner-prediction recovery schedule is now an `AWS::Scheduler::Schedule`, not a loose UTC EventBridge Rule.

- Start boundary: `2026-07-03T01:00:00-04:00`
- UTC start boundary: `2026-07-03T05:00:00Z`
- Cadence: `cron(0/15 * * * ? *)`
- Timezone: `America/New_York`
- Target Lambda: `MLBHotPullRecoveryFunction`
- Schedule policy: `ODDS_API_LINE_MOVEMENT_TO_MLB_WINNERS_EVERY_15_MIN_START_2026_07_03_1AM_ET`

The audited snapshot capture also gets a matching EventBridge Scheduler resource for 15-minute Odds API snapshot audit history.

## Prediction inputs

The winner engine uses stored Odds API pull history, specifically:

- de-vigged moneyline consensus probability;
- probability movement from earlier stored pulls to the latest pull;
- book count and book divergence;
- reversals;
- run-line movement and confirmation;
- pull depth.

## Deployment fix

The GitHub Actions deploy workflow now treats `ODDS_API_KEY` as required and SportsDataIO as optional. This matches the architecture: Odds API line movement is the core signal source; fundamentals are enrichment only.

## Smoke-test expectations

After deploy, the workflow checks:

- `/v1/health`
- `/v1/mlb/model/version`
- `/v1/mlb/today`
- `/v1/sources/mlb/status`
- `/v1/mlb/game-winners?store=false`
- `/v1/results/mlb/signal-learning?fetch_scores=false`
- `/v1/mlb/fundamentals/status?fetch=false`

## Remaining operator checks

Before the first 1:00 AM ET production window, verify:

1. GitHub secret `ODDS_API_KEY` is set.
2. AWS region secret is set.
3. The deployed CloudFormation stack contains `MLBWinnerPredictionEvery15From1amETSchedule`.
4. DynamoDB has new `MLB_HOT_PULL_RECOVERY#RUNS` items after schedule fire.
5. Pull history count for `PULLS#mlb#YYYY-MM-DD` increases on 15-minute boundaries.
