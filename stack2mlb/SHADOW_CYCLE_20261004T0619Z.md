# 2stackMLB shadow cycle 2026-10-04T06:19Z

promoted=false. Shadow-only. No KS1 p_home, lock, ledger, or SCHEMA write. No official model train.

Source: ks1-nightly artifact from run 37177406544, committed_ledger as_of 2026-10-04T01:37:01Z, night_date 2026-10-03.

Official KS1 metrics (read-only): Brier 0.23525678394092636, logloss 0.663739307781646, n=230, new_grades=1 (game 849835, p_home 0.5677, home_win=1). Locked rows 232; excluded no_bound_final: 823490, 849830.

Confidence taxonomy on official p_home (not a challenger score):
- conf>=0.60: 64 hit / 23 miss (n=87)
- 0.55-0.60: 30 hit / 32 miss (n=62)
- <0.55: 41 hit / 40 miss (n=81)

Read: mid-confidence band is coin-flip. High-confidence misses (23) are not a retrain signal. 2stackMLB stays unpromoted. BBS isolate-skip is already on main; this note does not patch ingestion.
