# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-24 20:08 EDT / 2026-09-25 00:08 UTC operator cycle:
- Isolate-skip already on main `ks1/daily.py`. Tests: `tests/ks1_phase5/test_bbs_identity_isolation.py`.
- This branch `ks1/daily.py` remains placeholder only. Do not merge this branch; do not patch main; do not copy this placeholder over main.
- Latest schedule: 36067244666 SUCCESS 22:24:28Z-22:37:12Z (~12.7m) run 744. Prior schedule 36046283717 SUCCESS 19:10:07Z-19:35:34Z (~25.5m) run 740.
- Latest attempt: schedule 36067244666. Age since last attempt start ~104m, since finish ~91m. HEALTH=CRON_GAP. Watchdog not started. PR 712 watchdog not activated. Did not dispatch mlb-research-ingestion.yml.
- Artifacts latest completed attempt: ks1-daily-36067244666, ks1-nightly-36067244666, ks1-loss-patterns-36067244666-1, mlb-research-ingestion-36067244666.
- No failed ingest this window. No isolate code change required.
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers.
- PR 710 open ready; SCHEMA/publish adds p_lgb, pick_status, selection_reason. PR 711 draft shadow-only. PR 712 merged closed; watchdog not activated. PR 713 draft isolate notes only.
- Daily 36067244666 date=2026-09-24: 12 rows, 10 confirmed / 2 projected. Sits remain NYY/NYM/CWS fights.
- Nightly 36067244666: official Brier=0.233759 n=177 new_grades=1. locked_rows=178 eligible_graded=177 excluded=1 no_bound_final (823411).
- Isolate already committed on main; 2stack walk-forward allowed on PR 711 only, not run this hour (artifact zip 403). No official train.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
