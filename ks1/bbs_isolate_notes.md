# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-19 08:04Z:
- Isolate-skip already on main `ks1/daily.py`. Tests: `tests/ks1_phase5/test_bbs_identity_isolation.py`.
- Branch HEAD `daily.py` is PLACEHOLDER vs main. Do not merge PR 713.
- Latest schedule 35425130291 FAILURE 05:53-05:57Z (~4m16s). Cause: `python -m ks1.daily --publish` abort/core dump exit 134 after scoring JSON; not unmatched BBS / missing_bbs_identity. No isolate-skip code change.
- Later dispatches recovered: 35427828584 success 06:53-06:57Z; 35430550882 success 07:53-07:59Z artifacts ks1-daily-35430550882 + ks1-nightly-35430550882.
- HEALTH=CRON_GAP: last schedule attempt 05:53Z, no later schedule event by 08:03Z (>70m). Watchdog not started. PR 712 closed, not activated.
- Latest daily 35430550882: 15 rows, 0 confirmed / 15 projected; BBS matched 15, exclusions []. DET@CWS, PHI@NYM, NYY@ARI remain sits.
- Nightly 35430550882: official Brier 0.2354 n=110 new_grades=0 status=no_new_final_grades locked_rows=110.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
