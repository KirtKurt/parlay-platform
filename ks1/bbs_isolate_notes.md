# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-19 11:05Z:
- Isolate-skip already on main `ks1/daily.py`. Tests: `tests/ks1_phase5/test_bbs_identity_isolation.py`.
- Branch HEAD `daily.py` is PLACEHOLDER vs main. Do not merge PR 713.
- Latest schedule 35438863367 SUCCESS 10:59-11:05Z (~5m59s). Artifacts: ks1-daily-35438863367, ks1-nightly-35438863367, mlb-research-ingestion-35438863367.
- Prior schedule 35425130291 FAILURE 05:53-05:57Z: `python -m ks1.daily --publish` abort/core dump exit 134 after scoring JSON; not unmatched BBS / missing_bbs_identity. No isolate-skip code change.
- Latest push 35432151456 SUCCESS 08:29-08:36Z (PR #991).
- HEALTH=OK. No CRON_GAP. Watchdog not started. PR 712 closed/merged, not activated.
- PR 710 open ready, SCHEMA adds p_lgb/pick_status/selection_reason. PR 711 draft shadow-only. PR 713 draft do-not-merge.
- Daily 35438863367: 15 rows, 0 confirmed / 15 projected; BBS matched 15, exclusions []. DET@CWS, PHI@NYM, NYY@ARI sits. BOS@TBR and SF@LAD projected_missing_starter.
- Nightly 35438863367: official Brier 0.2354 n=110 new_grades=0 status=no_new_final_grades locked_rows=110.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
