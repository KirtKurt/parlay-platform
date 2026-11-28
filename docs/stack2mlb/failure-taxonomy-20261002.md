# 2stackMLB shadow failure taxonomy (2026-10-02)

Shadow only. `promoted=false`. Not an official model. No KS1 `p_home`, lock, or ledger rewrite. No training.

Source: nightly artifact `ks1-nightly-36975048355` (schedule run 1030, success, started 2026-10-02T06:44:53Z). Prior read was `ks1-nightly-36965014872`. Ledger as_of `2026-10-02T06:51:40Z`. Status `no_new_final_grades`. `trained_LightGBM=false`. `published=false`.

## Official champion metrics (unchanged)
- Brier 0.235618
- logloss 0.664471
- n 227 graded locked rows
- new_grades 0
- eligible_graded_rows 227 / locked_rows 228
- excluded 1 (`823490`, `no_bound_final`)
- window as_of dates 2026-09-10 through 2026-09-27
- side hit 133/227 (0.586)

## Taxonomy (confidence = max(p_home, 1-p_home))
- coin (<0.55): n=80, Brier 0.2487, side hit 0.512
- lean (0.55-0.62): n=78, Brier 0.2524, side hit 0.526
- strong (>=0.62): n=69, Brier 0.2014, side hit 0.739
- strong-side misses: 23

Lean remains the worst slice. No retrain. Next shadow step stays chronological walk-forward of Elo/market-prior against this ledger, still unpromoted.

Today 2026-10-02 is a postseason off-day (NLDS starts 2026-10-03). Empty official slate (0 rows, 0 confirmed, 0 projected) is expected, not a BBS kill. NYY/NYM and CWS remain sits when they appear.
