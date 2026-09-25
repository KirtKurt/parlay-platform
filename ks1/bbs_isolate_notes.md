# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-25 12:09 EDT / 2026-09-25 16:09 UTC operator cycle:
- Isolate-skip already on main `ks1/daily.py`. Tests: `tests/ks1_phase5/test_bbs_identity_isolation.py`.
- This branch `ks1/daily.py` remains placeholder only. Do not merge this branch; do not patch main; do not copy this placeholder over main.
- Latest main ingest 36156690593 SUCCESS workflow_dispatch 15:48:47Z-15:57:53Z (~9.1m) run 765. Artifacts: ks1-daily-36156690593, ks1-nightly-36156690593, ks1-loss-patterns-36156690593-1, mlb-research-ingestion-36156690593.
- Latest schedule 36143235341 SUCCESS 13:47:41Z-14:04:33Z (~16.9m) run 763. Prior schedule 36109575938 SUCCESS 07:48:54Z-07:58:49Z (~9.9m) run 755.
- Age since last schedule start ~141m; last attempt (dispatch 765) ~21m. HEALTH=OK. Dispatch succeeding ~hourly. Watchdog not started. PR 712 watchdog not activated. Did not dispatch mlb-research-ingestion.yml.
- No failed ingest this window. No isolate code change required.
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers.
- PR 710 open ready; SCHEMA/publish adds p_lgb, pick_status, selection_reason. PR 711 draft shadow-only. PR 712 merged closed; watchdog not activated. PR 713 draft isolate notes only (dirty vs main).
- Daily 36156690593 date=2026-09-25: 16 rows, 1 confirmed / 15 projected. official_games=17. bbs_matched=15. bbs_identity_exclusions=2 unmatched BBS (d5cecc53 vs 823491/823489; 028b8776 vs 824703/824706). official exclusions missing_bbs_identity: 823489 (retained_previous), 824706. Sits remain NYY/NYM/CWS fights.
- Nightly 36156690593: new_grades=0 ledger_rows=184 locked_rows=184. Official Brier=0.235500 n=184. ACCURACY available from ledger, no new grades this tick.
- Isolate already committed on main; 2stack walk-forward allowed on PR 711 only, not run this hour. No official train.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
