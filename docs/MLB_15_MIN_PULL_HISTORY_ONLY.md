# MLB-B1.0 — 15-Minute Pull-History Only

MLB-B1.0 no longer uses T1/T2/T3/T4 snapshot comparisons for signal generation, individual game picks, or 3-leg parlay construction.

## Active rule

MLB signal generation reads only date-isolated `HOT` snapshots:

```text
PK = SPORT#mlb#DATE#YYYY-MM-DD
SK begins_with HOT#GAME_DATE#YYYY-MM-DD
```

That means the MLB model is built from rolling 15-minute pull history only.

## What is ignored

Legacy `T1`, `T2`, `T3`, and `T4` snapshots may exist historically in DynamoDB, but the MLB signal reader ignores them. They do not influence:

- movement deltas
- individual game-winner attempts
- MLB_STRONG / MLB_LEAN candidate formation
- 3-leg parlay construction
- proof-report movement visibility

## Why

The MLB model needs continuous pull-history behavior, not arbitrary fixed-time windows. The useful inputs are:

- latest 15-minute movement
- multi-pull velocity
- movement acceleration
- book agreement and disagreement
- run-line support or conflict
- market flatness / no-edge proof
- final result settlement after completion

## Settlement relationship

Postgame settlement remains separate. It grades completed games only after final scores are stored. Settlement is the answer key for the 15-minute pull-history algorithm; it does not change pregame ranking logic.

## Product rule

If there are fewer than two HOT snapshots for a game date, MLB-B1.0 returns no movement signal instead of falling back to T1/T2/T3/T4.
