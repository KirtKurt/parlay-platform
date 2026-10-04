# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-10-04 00:20 UTC operator cycle:
- Restored `ks1/daily.py` and `tests/ks1_phase4/test_daily.py` from main. The previous 8-byte placeholders would delete the publisher if this draft were merged.
- Main already calls `bbs_assignments(..., isolate_unmatched=True)` and records `missing_bbs_identity` exclusions. This branch is no longer a destructive placeholder.
- Did not patch main. Did not merge. Did not rewrite p_home, locks, or ledgers.

Do not merge without Kurt approval.
