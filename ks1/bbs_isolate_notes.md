# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-18 23:50Z:
- Isolate-skip already on main `ks1/daily.py` (`isolate_unmatched=True`). Tests: `tests/ks1_phase5/test_bbs_identity_isolation.py`.
- This branch HEAD `daily.py` remains PLACEHOLDER. Do not merge PR 713.
- Latest main ingest 35406733422 (dispatch, success, 8m32s) and last schedule 35398022337 (success, 14m44s). No BBS identity slate kill. bbs_identity_exclusions=[].
- HEALTH=CRON_GAP: last schedule 21:41Z; later hours covered only by dispatch. Watchdog not started.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
