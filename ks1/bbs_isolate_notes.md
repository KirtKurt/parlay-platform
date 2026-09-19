# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-19 02:16Z:
- Isolate-skip already on main `ks1/daily.py` (`isolate_unmatched=True`). Tests: `tests/ks1_phase5/test_bbs_identity_isolation.py`.
- This branch HEAD `daily.py` remains PLACEHOLDER. Do not merge PR 713.
- Latest main ingest 35412437501 (dispatch, success, 5m18s) artifacts ks1-daily-35412437501 + ks1-nightly-35412437501.
- Last schedule 35409065933 (success, 6m05s) artifacts ks1-daily-35409065933 + ks1-nightly-35409065933.
- No CRON_GAP vs last attempt (01:22Z). Watchdog not started. PR 712 watchdog not activated.
- No BBS identity slate kill. bbs_identity_exclusions=[].
- Nightly: official Brier 0.241 n=95 new_grades=0. 11 locked rows waiting no_bound_final.
- Slate 2026-09-18: 15/15 confirmed; NYY@ARI and PHI@NYM + DET@CWS remain sits.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
