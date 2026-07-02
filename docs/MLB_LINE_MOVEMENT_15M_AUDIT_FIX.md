# MLB 15-Minute Line-Movement Audit Fix

Date: 2026-07-02

## Requirement

The MLB platform must use Odds API line movement to:

1. Pull MLB market data every 15 minutes.
2. Store every pull as immutable pull/snapshot history.
3. Calculate line movement from consecutive pulls.
4. Store a game-winner prediction row for every MLB game on the covered slate.
5. Keep postgame settlement separate so completed games are scored only after final results are available.

## GitHub audit result

The repository already had the core AWS resources:

- `template.yaml` defines DynamoDB tables for snapshots, signals, predictions, and outcomes.
- `MLBAuditedPullFunction` is wired to `/v1/pull/mlb` and an EventBridge `rate(15 minutes)` HOT pull.
- `mlb_manual_pull.py` pulls `baseball_mlb` from The Odds API and stores HOT snapshots.
- `mlb_date_signal_api.py` reads only date-isolated HOT snapshots for movement deltas and winner attempts.

The gaps were operational, not conceptual:

- The 15-minute pull did not guarantee that predictions were stored after every HOT pull.
- The schedule had no explicit 1:00am ET start gate.
- The prediction rows did not expose a single canonical Odds API line-movement model version.
- The three-leg parlay attempt was returned by the API but not persisted after every pull.

## Fix installed

`hello_world/mlb_line_movement_15m_patch.py` is loaded by `hello_world/sitecustomize.py`.

It adds:

- `MLB-LINE-MOVE-WINNER-V2-2026-07-02` model version.
- Default scheduled start gate: `2026-07-03T01:00:00-04:00`.
- Optional override: `MLB_PULL_START_AT_ET`.
- Every successful HOT pull now runs `mlb_date_signal_api.hot_sides(..., store=True, include_no_edge=True)` for every returned MLB game date.
- Every game receives a stored prediction row:
  - line-movement hot side when the 15-minute move is material,
  - consensus favorite as a `NO_EDGE` attempted winner when the market is flat.
- Every successful HOT pull stores a three-leg parlay attempt when at least three MLB games are available.
- A signal-ledger audit row is written for prediction storage after each pull.

## Model input policy

Primary signal:

```text
Odds API h2h moneyline consensus probability movement across books between consecutive HOT snapshots.
```

Support signals:

- book agreement count,
- disagreement count,
- spread movement direction,
- latest consensus favorite,
- latest market-implied probability.

The model never needs T1/T2/T3/T4 static checkpoints for MLB winner selection. MLB winner attempts are built from 15-minute HOT pull history only.

## Runtime behavior

Scheduled EventBridge pull before the start gate:

```json
{
  "ok": true,
  "skipped": true,
  "skip_reason": "WAITING_FOR_1AM_ET_START_GATE"
}
```

Successful HOT pull after the start gate adds this block to the Lambda response:

```json
{
  "prediction_storage_after_pull": {
    "run_after_every_hot_pull": true,
    "pull_interval_minutes": 15,
    "results": []
  },
  "line_movement_pipeline": {
    "status": "CONNECTED",
    "flow": [
      "pull_odds",
      "store_snapshot",
      "calculate_line_movement",
      "store_game_winner_predictions",
      "store_parlay_attempt"
    ]
  }
}
```

## Validation checklist after deploy

1. Trigger `/v1/pull/mlb` manually after configuring `ODDS_API_KEY`.
2. Confirm the response has `live_pull_ok: true` and `date_isolated_stored` rows.
3. Wait for at least two HOT pulls, or run the manual pull twice.
4. Confirm `/v1/signals/mlb/deltas?game_date_et=YYYY-MM-DD` returns movement deltas.
5. Confirm `/v1/predictions/mlb/hot-sides?game_date_et=YYYY-MM-DD&store=true` stores game predictions.
6. Confirm DynamoDB contains `PRED#mlb#YYYY-MM-DD` rows with `line_movement_model_version`.
7. Confirm `/v1/results/mlb/status?slate_date_et=YYYY-MM-DD` only grades completed games.

## Notes

Final win/loss grading cannot be known until a game is completed. The 15-minute loop stores model scores and winner predictions. The results scheduler settles completed games later using final score data and does not grade live or unstarted games.
