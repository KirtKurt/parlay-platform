# 2stackMLB shadow walk-forward 2026-10-06T04:14Z

Source: ks1-nightly-37409579031 graded_ledger.json (run 37409579031). Shadow only. promoted=false. No official model trained. No p_home, lock, or ledger rewrite.

## Official KS1 (champion, not this stack)
- Brier 0.2353774129257362, logloss 0.6639793672853979, n=235
- locked_rows=236, new_grades=1 this catch-up (second Oct 6 grade already in ledger from prior tick)
- excluded: 823490 no_bound_final

## Chronological night Brier (graded_at date)
- 2026-10-03: n=2, 0.2185
- 2026-10-04: n=3, 0.1832
- 2026-10-05: n=1, 0.3579
- 2026-10-06: n=2, 0.2419
- Expanding Brier on all 235 scored locks: 0.235377

## This tick grades (not used to refit)
- 849834 CWS@CLE: p_home 0.418, home_win 0 (4-3). Side matched away. Projected sit remains.
- 849839 NYY@TB: p_home 0.444, home_win 1 (5-2). Side matched away and lost. Projected sit remains.

## Failure taxonomy
- Confident misses (|p-0.5|>=0.10 and wrong side): 23 / 235. Latest still 824543 (2026-09-27), p_home 0.834, home lost 6-9. One miss is not a retrain.
- Market unavailable on both 2026-10-05 published rows. No shadow attach to a fresh close.
- Do not promote 2stackMLB.
