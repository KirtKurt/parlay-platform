# 2stackMLB shadow status (not promoted)

Cycle: 2026-10-16T06:20Z operator hour.
Champion remains KS1-LGB+dual-Poisson. This file does not change p_home, locks, ledgers, or SCHEMA.

Official nightly artifact run 38855863276 (night_date 2026-10-15):
- status: not_due_or_already_completed
- new_grades: 1
- ledger eligible: 243
- locked_rows: 244
- official Brier: 0.2370383148151549 (n=243)
- logloss: 0.6673513738521984

Today slate (predictions.parquet, 1 row, projected):
- 849817 CLE vs TB: projected_missing_starter (lineups projected, confirmed=0)
- NYY/NYM and CWS remain sits
- market_status unavailable; no edge published

BBS isolate-skip is already on main (`missing_bbs_identity` / unmatched exclusions). No official model trained. No promotion.
Next shadow work: walk-forward and failure taxonomy only. Do not promote.
