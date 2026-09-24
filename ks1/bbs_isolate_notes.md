# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-24 16:05 EDT / 2026-09-24 20:05 UTC operator cycle:
- Isolate-skip already on main `ks1/daily.py`. Tests: `tests/ks1_phase5/test_bbs_identity_isolation.py`.
- This branch `ks1/daily.py` remains placeholder only. Do not merge this branch; do not patch main; do not copy this placeholder over main.
- Latest schedule: 36046283717 SUCCESS 19:10:07Z-19:35:34Z (~25.5m) run 740. Prior schedule 36017719787 SUCCESS 15:06:34Z-15:18:26Z (~11.9m) run 735.
- Latest attempt: schedule 36046283717. Age since last attempt ~55m. HEALTH=OK. Watchdog not started. PR 712 watchdog not activated. Did not dispatch mlb-research-ingestion.yml.
- Artifacts latest completed attempt: ks1-daily-36046283717, ks1-nightly-36046283717, ks1-loss-patterns-36046283717-1, mlb-research-ingestion-36046283717.
- No failed ingest this window. No isolate code change required. bbs_identity_exclusions=[].
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers.
- PR 710 open ready; SCHEMA/publish adds p_lgb, pick_status, selection_reason. PR 711 draft shadow-only. PR 712 merged closed; watchdog not activated. PR 713 draft isolate notes only.
- Daily 36046283717 date=2026-09-24: 12 rows, 7 confirmed / 5 projected. Sits remain NYY/NYM/CWS fights.
- Nightly 36046283717: official Brier=0.234309 n=173 new_grades=1. locked_rows=177 eligible_graded=173 excluded=4 no_bound_final (824059,824623,822842,824298).
- Isolate already committed on main; 2stack walk-forward allowed on PR 711 only, not run this hour. No official train.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
