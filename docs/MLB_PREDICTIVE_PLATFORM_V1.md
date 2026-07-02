# MLB Predictive Platform V1

## Installed goal

MLB Predictive Platform V1 is the production path for using The Odds API line movement to pull, record, score, and predict MLB game winners every 15 minutes.

The live scheduled handler is:

- `hello_world/mlb_manual_pull.py`
- SAM resource: `MLBAuditedPullFunction`
- manual endpoint: `POST /v1/pull/mlb`
- scheduled event: `MLBHotEvery15Min`

## What was found in GitHub before this patch

1. `template.yaml` already had an MLB Lambda with a `rate(15 minutes)` schedule.
2. `odds_live_ingestion.py` already mapped app sport `mlb` to The Odds API sport key `baseball_mlb`.
3. `mlb_manual_pull.py` already stored HOT MLB snapshots under date-isolated DynamoDB partitions.
4. The game-winner engine, `mlb_game_winner_engine.py`, did not read those snapshot partitions. It reads canonical pull history from `inqsi_pull_history.query_pulls("mlb", slate_date)`, which expects `PULLS#mlb#YYYY-MM-DD` records.
5. That meant a scheduled MLB pull could record snapshots while the winner engine still had no canonical pull history to score.

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

## Time-zone and cadence contract

Scheduled, non-HTTP events are gated by:

```text
MLB_PULL_START_AT_ET=2026-07-03T01:00:00-04:00
MLB_SCHED_INTERVAL_MINUTES=15
```

The SAM patch script now changes `MLBHotEvery15Min` from `rate(15 minutes)` to quarter-hour cron:

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

## Data-source policy

MLB V1 does not use SportsDataIO.

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

## Storage contract

The V1 handler writes to the following DynamoDB key families:

```text
SPORT#mlb
SPORT#mlb#DATE#YYYY-MM-DD
PULLS#mlb#YYYY-MM-DD
ML_FEATURE#mlb#YYYY-MM-DD
PRED#mlb#YYYY-MM-DD
GAME_WINNERS#mlb#YYYY-MM-DD
AUDIT#mlb#YYYY-MM-DD
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

## Validation checklist after deployment

Manual smoke test:

```bash
curl -X POST "$API_URL/v1/pull/mlb" \
  -H 'content-type: application/json' \
  -d '{"t":"HOT","run":"manual_v1_smoke","days_ahead":1,"force":true}'
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
```

## Notes

- V1 does not claim guaranteed betting outcomes. It produces transparent market-derived predictions and stores every pull needed to audit why each prediction was made.
- The Odds API key must be present as `ODDS_API_KEY` on the deployed Lambda.
- The schedule only becomes real in production after the SAM stack is deployed from this branch or the branch is merged and deployed by CI.
