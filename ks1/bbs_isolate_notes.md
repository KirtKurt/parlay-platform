# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-24 23:07 EDT / 2026-09-25 03:07 UTC operator cycle:
- Isolate-skip already on main `ks1/daily.py`. Tests: `tests/ks1_phase5/test_bbs_identity_isolation.py`.
- This branch `ks1/daily.py` remains placeholder only. Do not merge this branch; do not patch main; do not copy this placeholder over main.
- Latest schedule: 36082319345 SUCCESS 01:30:32Z-01:51:00Z (~20.5m) run 748. Artifacts: ks1-daily-36082319345, ks1-nightly-36082319345, ks1-loss-patterns-36082319345-1, mlb-research-ingestion-36082319345.
- Prior schedule: 36067244666 SUCCESS 22:24:28Z-22:37:12Z (~12.7m) run 744.
- Latest push on main: 35771536258 SUCCESS 2026-09-22 (PR #1012). No failed ingest this window.
- Age since last schedule start ~97m, since finish ~76m. HEALTH=CRON_GAP. Watchdog not started. PR 712 watchdog not activated. Did not dispatch mlb-research-ingestion.yml.
- No isolate code change required.
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers.
- PR 710 open ready; SCHEMA/publish adds p_lgb, pick_status, selection_reason. PR 711 draft shadow-only. PR 712 merged closed; watchdog not activated. PR 713 draft isolate notes only.
- Daily 36082319345 date=2026-09-24: 12 rows, 12 confirmed / 0 projected. Sits remain NYY/NYM/CWS fights.
- Nightly 36082319345: official Brier=0.234047 n=179 new_grades=0. locked_rows=183 eligible_graded=179 excluded=4 no_bound_final (823493,824866,823087,824950). status=no_new_final_grades.
- Isolate already committed on main; 2stack walk-forward allowed on PR 711 only, not run this hour. No official train.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
