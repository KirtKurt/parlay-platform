# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-19 04:05Z:
- Isolate-skip already on main `ks1/daily.py`. Tests: `tests/ks1_phase5/test_bbs_identity_isolation.py`.
- This branch HEAD `daily.py` remains PLACEHOLDER. Do not merge PR 713.
- Latest main ingest 35418386447 (dispatch, success, ~4m57s) artifacts ks1-daily-35418386447 + ks1-nightly-35418386447.
- Last schedule 35409065933 (success, ~6m05s) artifacts ks1-daily-35409065933 + ks1-nightly-35409065933.
- No CRON_GAP vs last attempt (03:23Z). Watchdog not started. PR 712 watchdog not activated.
- No BBS identity slate kill. bbs_identity_exclusions=[].
- Nightly 2026-09-18: official Brier 0.239 n=106 new_grades=6. locked_rows=110; 4 waiting no_bound_final (823977,823252,825032,823898).
- Slate 2026-09-18: 15 rows, 15 confirmed / 0 projected; NYY@ARI, PHI@NYM, DET@CWS remain sits.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
