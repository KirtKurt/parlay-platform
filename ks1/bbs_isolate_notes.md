# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-24 04:07 EDT / 2026-09-24 08:07 UTC operator cycle:
- Isolate-skip already on main `ks1/daily.py` (`isolate_unmatched`). Tests: `tests/ks1_phase5/test_bbs_identity_isolation.py`.
- This branch `ks1/daily.py` is placeholder only. Do not merge this branch; do not patch main; do not copy this placeholder over main.
- Latest schedule: 35957395952 SUCCESS 04:51:04Z-04:58:31Z (~7.4m) run 723. Latest attempt: dispatch 35972033164 SUCCESS 07:52:51Z-07:58:47Z (~5.9m) run 726. Age since last attempt ~15m. HEALTH=OK. Watchdog not started. PR 712 watchdog not activated. Did not dispatch mlb-research-ingestion.yml.
- Artifacts latest: ks1-daily-35972033164, ks1-nightly-35972033164, ks1-loss-patterns-35972033164-1, mlb-research-ingestion-35972033164.
- No failed ingest this window. No isolate code change required. bbs_identity_exclusions=[] exclusions=[].
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers.
- PR 710 open ready; SCHEMA/publish adds p_lgb, pick_status, selection_reason. PR 711 draft shadow-only. PR 712 merged closed; watchdog not activated. PR 713 draft isolate notes only.
- Daily 35972033164 date=2026-09-24: 12 rows, 0 confirmed / 12 projected. Sits remain NYY/NYM/CWS fights.
- Nightly 35972033164: status=no_new_final_grades. official Brier=0.234602 n=172 new_grades=0. locked_rows=172 eligible_graded=172 excluded=[].
- Isolate already committed on main; 2stack walk-forward allowed on PR 711 only, not run this hour. No official train.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
