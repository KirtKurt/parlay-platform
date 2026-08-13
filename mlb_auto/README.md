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

EventBridge heartbeat: every **5 minutes**. The heartbeat does not imply an Odds API charge. `schedule_controller.decide_pull` chooses the actual API cadence:

- >24h to first pitch: 60m
- 12–24h: 30m
- 6–12h: 15m
- 2–6h: 10m
- <=2h: 5m
- increased volatility / missing market coverage: accelerate to 5–10m
- repeated empty responses / low quota: back off to 30–60m

The controller can force immediate pulls for stale data, missing coverage, repair actions, or material schedule changes.

## Odds API coverage

The system consumes the official `baseball_mlb` events/odds APIs. Featured `h2h`, `spreads`, and `totals` are collected slate-wide. For every event, `/events/{id}/markets` is used to discover deeper markets, and useful supported markets are queried per event. Raw event-market payloads are preserved even when they cannot safely become team-attributed model features.

Game-winner ML may use full-game market probabilities, movement, bookmaker disagreement, period/innings market depth, team-total/alternate-line availability, and safely attributable prop depth. Player props are never guessed onto a team.

## Autonomous ML + repair

Settlement creates labels only from exact provider event IDs and immutable pregame feature vectors. Training is chronological, challengers are versioned, and promotion is gated. The repair controller may retry only this stack's ingestion, market discovery, settlement, training, or champion fallback. It cannot mutate Tennis or any existing MLB system.
