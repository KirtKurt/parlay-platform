# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-18:
- Isolate-skip is already committed on main `ks1/daily.py` (`isolate_unmatched=True` at predict; missing official BBS -> exclusions).
- Tests on main: `tests/ks1_phase5/test_bbs_identity_isolation.py`.
- This branch HEAD `daily.py` remains PLACEHOLDER. Do not merge PR 713.
- Latest ingest (push 35298356204, schedule 35290711743) succeeded; no BBS identity slate kill.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
