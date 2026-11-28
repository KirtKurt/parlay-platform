# 2stackMLB Shadow Research Status — 2026-11-28

Champion remains KS1-LGB+dual-Poisson. 2stackMLB is shadow-only. promoted=false.

## Current
- Official graded locks: n=250, Brier=0.2375 (from nightly 46125475136).
- No new grades this cycle.
- Offseason: official_games=0 on 2026-11-28.

## Safe next research (do not train official model)
1. Chronological walk-forward on graded_ledger + locked prediction rows.
2. Failure taxonomy on existing graded misses (one miss is not a retrain).
3. Shadow attach of Elo/Markov/market-prior/GLM only under mlb/ks1/stack2mlb-shadow-v1/.

No SCHEMA change. No p_home/lock/ledger rewrite. No merge. No watchdog activation.
