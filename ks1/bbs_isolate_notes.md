# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-17:
- Isolate-skip is already on main `ks1/daily.py` (`isolate_unmatched=True` at predict; missing official BBS -> exclusions).
- This branch HEAD `daily.py` is PLACEHOLDER. Do not merge PR 713.
- Tests live on main: `tests/ks1_phase5/test_bbs_identity_isolation.py`.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
