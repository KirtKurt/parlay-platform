# Slip Scanner Structured Risk Contract

This contract implements the V1 direction: **Sport → Game → Selection → Scan**.
Screenshot/OCR ingestion is not a core dependency.

## Input
Each scan selection must resolve to structured identifiers before risk assembly:
- sport / league
- event_id and start time
- home / away participants
- market_type and selection
- line and current price when applicable
- optional user-selected sportsbook/price
- prediction-time fundamentals probability
- prediction-time market-aware probability
- de-vig sportsbook-implied probability
- timestamped market movement summary
- confirmed and monitoring sport-specific signals

BBD/provider data is translated into factual signals; provider names are provenance,
not the consumer explanation.

## Output
`inqsi_intelligence.slip_risk.build_binary_risk_assessment` returns:
- diagnostic risk score/level
- short consumer message
- fundamentals / market-aware / sportsbook-implied probabilities
- market influence and fundamentals-vs-market disagreement
- movement counts/window and better-price warning
- confirmed vs monitoring signals
- basic / mid / advanced disclosure groups

## Safety / authority
This layer is authority-neutral and does not change any sport's serving authority,
lock, calibration, qualification, promotion, or deployment behavior. Risk scores are
not win probabilities and the initial deterministic score must be prospectively
graded before learned weights replace it.

## Scale
Event intelligence should be precomputed/cached and reused across users. Per-user
work should primarily bind selected markets/prices to existing event intelligence.
