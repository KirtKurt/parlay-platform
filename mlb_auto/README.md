# MLB Auto — autonomous sibling algorithm

A new MLB game-winner platform modeled on the autonomous Tennis lifecycle while remaining runtime- and data-isolated from Tennis and every existing MLB algorithm.

## Isolation contract

- Provider sport: `baseball_mlb`
- Internal namespace: `mlb_auto`
- Dedicated stack: `parlay-platform-mlb-auto-prod`
- Dedicated tables/bucket/functions/schedules/model registry only
- Existing `parlay-platform-dev` and `parlay-platform-tennis-ml-prod` are protected/read-only and fingerprinted before/after deployment.
- No imports from `hello_world/mlb_*` or Tennis runtime modules.

## Hard autonomy requirement

Autonomous evolution is required behavior, not an optional later enhancement. Within the `mlb_auto` sandbox the system continuously has authority to adapt its own:

- probability model and challenger configuration;
- feature selection and feature interactions;
- thresholds and confidence calibration;
- data-collection cadence;
- signal weights;
- market usage and market-importance estimates;
- training windows and retraining triggers;
- repair behavior;
- regime detection;
- signal and pattern discovery.

The learner must evaluate newly observed pregame MLB numeric signals without waiting for a developer to pre-name them. Candidate features are coverage-checked, leakage-filtered, ranked on the training partition only, evaluated chronologically, and admitted to a champion only when the challenger passes validation and calibration gates. Interaction features may be generated automatically to identify combinations that matter even when individual signals are weak.

The immutable boundary is intentionally outside the learner: raw evidence, T−45 locks, settlement truth, audit records, sport isolation, and the protected Tennis/existing-MLB stacks cannot be rewritten by self-improvement.

## Autonomous cadence

EventBridge wakes the controller every **5 minutes**. The controller autonomously decides whether a fresh Odds API collection is valuable enough to run. **API cost, quota conservation, or remaining credits are not decision inputs.**

Pull urgency is driven by time remaining to the next MLB first pitch, observed line/probability volatility, newly listed or materially changed games, missing market coverage, recent signal changes, stale or incomplete snapshots, and repair requests from the system itself.

The operating intervals are 5/10/15/30/60 minutes, selected from information value. As first pitch approaches or markets move materially, the system naturally converges toward 5-minute collection. It may force an immediate pull whenever state is stale, coverage changes, or repair is required. There is no fixed daily start clock.

## Odds API coverage

The system consumes the official `baseball_mlb` event, odds, score, participant, event-market, and supported historical interfaces. Featured `h2h`, `spreads`, and `totals` are collected slate-wide. Per-event market discovery identifies additional MLB markets and preserves their raw payloads for audit and feature research. Data that cannot be safely attributed to a team remains available as market-level information rather than being guessed onto a side.

Game-winner ML can learn from full-game market probabilities, spreads, totals, alternate lines, team totals, period/innings information, safely attributable pitcher/batter information, market breadth/depth, movement, velocity, acceleration, volatility, reversals, bookmaker disagreement, timing effects, interactions, and newly discovered leakage-safe pregame measurements.

## Model readiness and picks

The system does not have a required number of daily picks and does not force a selection. It evaluates every MLB game. Until a challenger has enough clean chronological evidence to pass calibration/generalization gates, predictions remain bootstrap/shadow. Once a champion is qualified, it becomes the probability authority automatically. A game is surfaced only when the current champion's learned confidence/readiness policy supports it; otherwise the correct output is PASS/NO PLAY.

Training volume, validation quality, calibration, feature stability, challenger-vs-champion evidence, and regime evidence determine readiness. API spending does not.

## Autonomous ML + repair

Settlement creates labels only from exact provider event IDs and immutable pregame feature vectors. Training is chronological, challengers are versioned, and promotion is gated. The repair controller may retry only this stack's ingestion, market discovery, settlement, training, champion fallback, and autonomous model-search workflow. It cannot mutate Tennis or any existing MLB system.
