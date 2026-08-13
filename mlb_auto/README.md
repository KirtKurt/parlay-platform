# MLB Auto — autonomous sibling algorithm

A new MLB game-winner platform modeled on the autonomous Tennis lifecycle while remaining runtime- and data-isolated from Tennis and every existing MLB algorithm.

## Isolation contract

- Provider sport: `baseball_mlb`
- Internal namespace: `mlb_auto`
- Dedicated stack: `parlay-platform-mlb-auto-prod`
- Dedicated tables/bucket/functions/schedules/model registry only
- Existing `parlay-platform-dev` and `parlay-platform-tennis-ml-prod` are protected/read-only and fingerprinted before/after deployment.
- No imports from `hello_world/mlb_*` or Tennis runtime modules.

## Autonomous cadence

EventBridge wakes the controller every **5 minutes**. The controller autonomously decides whether a fresh Odds API collection is valuable enough to run. **API cost, quota conservation, or remaining credits are not decision inputs.**

Pull urgency is driven by:

- time remaining to the next MLB first pitch;
- observed line/probability volatility;
- newly listed or materially changed games;
- missing market coverage;
- recent signal changes;
- stale or incomplete snapshots;
- repair requests from the system itself.

The operating intervals are 5/10/15/30/60 minutes, selected from information value. As first pitch approaches or markets move materially, the system naturally converges toward 5-minute collection. It may force an immediate pull whenever state is stale, coverage changes, or repair is required.

There is no fixed daily start clock. The controller discovers the MLB slate continuously and chooses when useful collection should begin and how frequently it should continue.

## Odds API coverage

The system consumes the official `baseball_mlb` events/odds APIs. Featured `h2h`, `spreads`, and `totals` are collected slate-wide. For every event, `/events/{id}/markets` is used to discover deeper markets, and useful supported markets are queried per event. Scores, participants, and available historical snapshots are also supported. Raw event-market payloads are preserved even when they cannot safely become team-attributed model features.

Game-winner ML may use full-game market probabilities, movement, bookmaker disagreement, period/innings market depth, team-total/alternate-line availability, safely attributable pitcher/batter market information, market breadth, and temporal behavior. Player props are never guessed onto a team.

## Model readiness and picks

The system does not have a required number of daily picks and does not force a selection. It evaluates every MLB game. Until a Tennis-style challenger has enough clean chronological evidence to pass calibration/generalization gates, predictions remain bootstrap/shadow. Once a champion is qualified, it becomes the probability authority automatically. A game is surfaced as a pick only when the current champion's confidence/readiness policy supports it; otherwise the correct output is PASS/NO PLAY.

Training volume, validation quality, calibration, feature stability, and challenger-vs-champion evidence determine readiness. API spending does not.

## Autonomous ML + repair

Settlement creates labels only from exact provider event IDs and immutable pregame feature vectors. Training is chronological, challengers are versioned, and promotion is gated. The repair controller may retry only this stack's ingestion, market discovery, settlement, training, or champion fallback. It cannot mutate Tennis or any existing MLB system.
