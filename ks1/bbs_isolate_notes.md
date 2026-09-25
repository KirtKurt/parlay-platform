# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-24 21:07 EDT / 2026-09-25 01:07 UTC operator cycle:
- Isolate-skip already on main `ks1/daily.py`. Tests: `tests/ks1_phase5/test_bbs_identity_isolation.py`.
- This branch `ks1/daily.py` remains placeholder only. Do not merge this branch; do not patch main; do not copy this placeholder over main.
- Latest schedule: 36067244666 SUCCESS 22:24:28Z-22:37:12Z (~12.7m) run 744. Artifacts: ks1-daily-36067244666, ks1-nightly-36067244666.
- Latest attempt: dispatch 36077511694 SUCCESS 00:26:06Z-00:35:22Z (~9.3m) run 746. Artifacts: ks1-daily-36077511694, ks1-nightly-36077511694, ks1-loss-patterns-36077511694-1, mlb-research-ingestion-36077511694.
- Age since last attempt start ~41m, since finish ~32m. HEALTH=OK. Watchdog not started. PR 712 watchdog not activated. Did not dispatch mlb-research-ingestion.yml.
- No failed ingest this window. No isolate code change required.
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers.
- PR 710 open ready; SCHEMA/publish adds p_lgb, pick_status, selection_reason. PR 711 draft shadow-only. PR 712 merged closed; watchdog not activated. PR 713 draft isolate notes only.
- Daily 36077511694 date=2026-09-24: 12 rows, 12 confirmed / 0 projected. Sits remain NYY/NYM/CWS fights.
- Nightly 36077511694: official Brier=0.233759 n=177 new_grades=0. locked_rows=181 eligible_graded=177 excluded=4 no_bound_final (823411,824707,823493,824866). status=no_new_final_grades.
- Isolate already committed on main; 2stack walk-forward allowed on PR 711 only, not run this hour. No official train.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
