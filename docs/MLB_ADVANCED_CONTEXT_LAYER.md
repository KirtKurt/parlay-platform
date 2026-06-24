# MLB-B1.0 Advanced Context Layer

This layer wraps the existing MLB 15-minute HOT pull-history algorithm with explicit advanced baseball context and eligibility scoring.

## Core rule

MLB-B1.0 remains a 15-minute pull-history model. Advanced context does not replace the market engine. It upgrades, blocks, or explains eligibility.

```text
15-minute HOT pull history
+ advanced MLB context
+ postgame settlement
= individual game proof
= stronger 3-leg parlay eligibility
```

## Required advanced fields

A leg is not `ADVANCED_ELIGIBLE` until all of the following are connected and populated:

- FIP / xFIP
- wRC+
- starter handedness splits
- confirmed probable pitchers
- bullpen fatigue
- confirmed lineups
- weather / wind / roof status
- ballpark factors
- injuries / late scratches / news
- public betting handle
- closing-line value tracking

## What The Odds API supplies

The Odds API is used for:

- 15-minute odds snapshots
- moneyline, spread/run-line, and total movement
- bookmaker agreement/disagreement
- current price validation
- final scores where available through the scores endpoint
- closing-line value once a closing snapshot is frozen and settlement runs

The Odds API is not the source for:

- FIP
- xFIP
- wRC+
- confirmed lineups
- injuries/news
- weather/wind/roof
- public betting handle

## Current connected status

- 15-minute odds pull history: connected
- settlement scores: connected
- probable pitchers: partial through MLB Stats API schedule hydrate
- venue: partial through MLB Stats API schedule hydrate
- FIP/xFIP: source required
- wRC+: source required
- starter handedness splits: source required
- bullpen fatigue: source required
- confirmed lineups: source required
- weather/wind/roof: source required
- ballpark factors: source required
- injuries/news: source required
- public betting handle: source required
- CLV: schema connected, pending closing snapshot and settlement

## Eligibility behavior

Market-only rows may still be produced for research and settlement learning.

Premium algorithm eligibility is stricter:

```text
advanced_eligible = false
```

until all required context components are present.

This prevents the platform from pretending missing data exists.

## Deployed surfaces

The existing MLB source status and hot-sides endpoints now expose advanced context:

```text
GET /v1/sources/mlb/status
GET /v1/predictions/mlb/hot-sides?game_date_et=YYYY-MM-DD&store=true
```

Each prediction row includes:

```text
advanced_context
advanced_eligible
advanced_blockers
```

The 3-leg parlay object includes:

```text
advanced_context.advanced_eligible
advanced_context.blockers
advanced_context.leg_contexts
```
