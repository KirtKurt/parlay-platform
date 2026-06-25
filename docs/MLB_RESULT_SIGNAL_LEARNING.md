# MLB result-signal learning

This layer turns final MLB outcomes into winner-labeled signal rows.

## Storage

Rows are written to the signal ledger table:

- Table: `parlay_platform_signal_ledger`
- Env var: `SIGNAL_LEDGER_TABLE`
- PK pattern: `RESULT_SIGNAL#mlb#YYYY-MM-DD`
- Game row SK pattern: `GAME#<game_key>`
- Summary SK pattern: `SUMMARY#<timestamp>`

## What each row captures

Each final game row includes:

- winner team
- winner side: home/away
- final score
- margin
- total runs
- prediction status
- predicted team if available
- HOT side
- HOT team
- HOT delta
- whether HOT side matched the winner
- whether favorite matched the winner
- home/away movement deltas
- spread signal
- total signal
- book agreement/divergence information
- previous/latest consensus
- reason codes
- advanced blockers/source-required fields

## Safety rules

- Only final/completed outcomes are converted to settled rows.
- Live, postponed, suspended, delayed, or missing-final games remain ungraded.
- Missing advanced inputs are marked source-required and are not inferred.
- Weight changes are not automatic.
- Minimum sample gate is 30 settled rows before even considering weight adjustment.

## API routes

The settlement deployment patch exposes:

- `GET /v1/results/mlb/result-signals?date=YYYY-MM-DD`
- `POST /v1/results/mlb/result-signals?date=YYYY-MM-DD`
- `GET /v1/mlb/result-signals?date=YYYY-MM-DD`
- `POST /v1/mlb/result-signals?date=YYYY-MM-DD`

POST builds/stores the result-signal rows. GET reads the latest rows and summaries.

## Scheduled behavior

The MLB settlement scheduler now performs:

1. final-score settlement
2. prediction/parlay grading
3. observe-only signal-learning report
4. winner-labeled result-signal row creation
