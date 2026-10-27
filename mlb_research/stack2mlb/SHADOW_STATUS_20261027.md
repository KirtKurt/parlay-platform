# 2stackMLB shadow status (not promoted)

Cycle: 2026-10-27T10:15Z operator hour.
Champion remains KS1-LGB+dual-Poisson. This file does not change p_home, locks, ledgers, or SCHEMA.

Official nightly artifact run 40481518643 (night_date ~2026-10-27):
- status: no_new_final_grades
- new_grades: 0
- ledger_rows: 248
- official Brier: 0.2380014254777101 (n=248)
- logloss: 0.6693401252902416

Today slate (predictions.parquet, 1 row, projected_missing_starter):
- TB vs MIL (game 849803): projected, confirmed_lineups=0, with_market=0
- NYY/NYM and CWS remain sits
- bbs_identity_exclusions: []
- bbs_matched_games: 1

BBS isolate-skip already committed on main (tests/ks1_phase5/test_bbs_identity_isolation.py; unmatched continue, missing go to exclusions, hard errors for duplicates/schema). No official model trained. No promotion.
Next shadow work: walk-forward and failure taxonomy only. Do not promote.
