# 2stackMLB shadow snapshot 2026-10-04

promoted: false
champion: KS1-LGB+dual-Poisson
trained_official_model: false

Source: ks1-nightly-37188133895 / ks1-daily-37188133895 (main dispatch success). Not a ledger rewrite.

## Official champion metrics (read-only)
- official Brier: 0.2350617033215114
- logloss: 0.6633460578789974
- n graded locks: 231
- locked_rows admission: 232
- new_grades this tick: 0
- pick accuracy: 0.5887445887445888 (136/231)
- calibration_status: deferred_to_next_nightly

## Failure taxonomy (this tick, not a retrain signal)
- slate rows: 2, both projected, confirmed_lineups: 0
- MIL-SD 849825 projected; both starters probable; market unavailable
- LAD-ATL 849823 projected_missing_starter (away starter missing); market unavailable
- odds_event_id empty on both; market_status unavailable
- bbs_identity_exclusions: 0 (isolate path already on main; no unmatched kill)
- NYY/NYM and CWS not on slate (sits)

## Shadow attach gate
Do not attach or promote. Market prior cannot be scored while Odds API identity is empty. Walk-forward stays on graded locks only; one missing-starter projected row is not a model failure.
