# 2stackMLB shadow failure taxonomy (2026-10-02)

Shadow only. `promoted=false`. Not an official model. No KS1 `p_home`, lock, or ledger rewrite. No training.

Source: nightly artifact `ks1-nightly-36965014872` committed ledger (read-only). Nightly status `not_due_or_already_completed`. Ledger as_of `2026-10-01T06:11:45Z`.

## Official champion metrics (unchanged)
- Brier 0.235618
- logloss 0.664471
- n 227 graded locked rows
- new_grades 0
- excluded 1 (`823490`, `no_bound_final`)
- window as_of dates 2026-09-10 through 2026-09-27

## Taxonomy (confidence = max(p_home, 1-p_home))
- coin (<0.55): n=80, Brier 0.2487, side hit 0.512
- lean (0.55-0.62): n=78, Brier 0.2524, side hit 0.526
- strong (>=0.62): n=69, Brier 0.2014, side hit 0.739
- strong-side misses: 23

Lean is the worst slice. A one-miss retrain is not indicated. Next shadow step is chronological walk-forward of Elo/market-prior against this ledger, still unpromoted.

Today 2026-10-02 is a postseason off-day (NLDS starts 2026-10-03). Empty official slate is expected, not a BBS kill.
