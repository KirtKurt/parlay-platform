# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-19 15:11Z EDT / 15:11 UTC:
- Isolate-skip already on main `ks1/daily.py` (`isolate_unmatched=True`). Tests: `tests/ks1_phase5/test_bbs_identity_isolation.py`.
- This branch `daily.py` restored from main (was PLACEHOLDER `SEE_FILE`). Do not merge PR 713; main already has isolate.
- Latest schedule 35448691942 SUCCESS 14:25-14:31Z (~5m45s). Artifacts: ks1-daily-35448691942 / ks1-nightly-35448691942 / mlb-research-ingestion-35448691942.
- Last schedule attempt 14:25Z; now 15:11Z (<70m). HEALTH=OK. Watchdog not started. Did not dispatch mlb-research-ingestion.yml.
- Prior schedule 35438863367 SUCCESS 10:59-11:05Z. Failed schedule 35425130291 05:53Z: abort/core dump in `ks1.daily --publish` (exit 134), not unmatched BBS.
- PR 710 open ready; SCHEMA adds p_lgb/pick_status/selection_reason. PR 711 draft shadow-only. PR 712 merged; watchdog not activated. PR 713 draft do-not-merge.
- Daily 35448691942: 15 rows, 0 confirmed / 15 projected; DET@CWS, PHI@NYM, NYY@ARI sits. Two projected_missing_starter.
- Nightly 35448691942: official Brier 0.2354 n=110 new_grades=0 status=no_new_final_grades locked_rows=110.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
