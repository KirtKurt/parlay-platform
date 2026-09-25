# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-25 04:05 EDT / 2026-09-25 08:05 UTC operator cycle:
- Isolate-skip already on main `ks1/daily.py` (`isolate_unmatched=True`). Tests: `tests/ks1_phase5/test_bbs_identity_isolation.py`.
- This branch `ks1/daily.py` remains placeholder only. Do not merge this branch; do not patch main; do not copy this placeholder over main.
- Latest main ingest 36109575938 SUCCESS schedule 07:48:54Z-07:58:49Z (~9.9m) run 755. Artifacts: ks1-daily-36109575938, ks1-nightly-36109575938, ks1-loss-patterns-36109575938-1, mlb-research-ingestion-36109575938.
- Prior schedule 36082319345 SUCCESS 01:30:32Z-01:51:00Z (~20.5m) run 748. Age since last schedule start ~17m. HEALTH=OK. Watchdog not started. PR 712 watchdog not activated. Did not dispatch mlb-research-ingestion.yml.
- No failed ingest this window. No isolate code change required.
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers.
- PR 710 open ready; SCHEMA/publish adds p_lgb, pick_status, selection_reason. PR 711 draft shadow-only. PR 712 merged closed; watchdog not activated. PR 713 draft isolate notes only (dirty vs main).
- Daily 36109575938 date=2026-09-25: 16 rows, 0 confirmed / 16 projected (11 projected + 5 projected_missing_starter). official_games=17. bbs_matched=15. bbs_identity_exclusions=2 unmatched BBS (d5cecc53 vs 823491/823489; 028b8776 vs 824703/824706). official exclusions missing_bbs_identity: 823489 (retained_previous), 824706. Sits remain NYY/NYM/CWS fights.
- Nightly 36109575938: status=no_new_final_grades published=false new_grades=0 ledger_rows=184 locked_rows=184. Official Brier=0.235500 n=184. ACCURACY available from ledger, no new grades this tick.
- Isolate already committed on main; 2stack walk-forward allowed on PR 711 only, not run this hour. No official train.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
