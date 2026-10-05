# 2stackMLB shadow status (not promoted)

Cycle: 2026-10-05T09:15Z operator hour.
Champion remains KS1-LGB+dual-Poisson. This file does not change p_home, locks, ledgers, or SCHEMA.

Official nightly artifact run 37285929892:
- status: no_new_final_grades
- new_grades: 0
- ledger_rows: 233
- locked_rows: 234 (one excluded: 823490 no_bound_final)
- official Brier: 0.23532102537089594 (n=233)
- pick accuracy: 0.5879828326180258 (137/233)

Today slate (predictions.parquet, 2 rows, both projected):
- 849834 CLE vs CWS: sit (CWS fight; lineups projected)
- 849839 TB vs NYY: sit (NYY fight; lineups projected)
- market_status unavailable on both; no edge published

BBS isolate-skip is already on main (`missing_bbs_identity`). No official model trained.
Next shadow work: walk-forward and failure taxonomy only. Do not promote.
