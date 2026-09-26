# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-25 20:00 EDT / 2026-09-26 00:00 UTC operator cycle:
- Isolate-skip already on main `ks1/daily.py`. Tests: `tests/ks1_phase5/test_bbs_identity_isolation.py`.
- This branch `ks1/daily.py` remains placeholder only. Do not merge this branch; do not patch main; do not copy this placeholder over main.
- Latest schedule 36196228493 FAILURE 22:20:32Z-22:29:36Z (~9.1m) run 772. Artifacts: ks1-nightly-36196228493, ks1-input-identities-36196228493; no ks1-daily.
- Latest overall 36200640894 dispatch FAILURE 23:20:42Z-23:37:55Z (~17.2m) run 773. Artifacts: ks1-nightly-36200640894, ks1-input-identities-36200640894, ks1-loss-patterns-36200640894-1; no ks1-daily.
- Cause (773 ingest refresh): `ValueError: multiple BBS IDs map to one official game` in `ks1.daily.bbs_assignments` line 317. Nightly completed_catchup first (new_grades=1, ledger_rows=186).
- Collision (kept hard-fail): official 824703 CHC@BOS 2026-09-25T17:05Z bound by two finished BBS IDs at same kickoff: `e41f7855` and `fceec9f8`. Not unmatched/ambiguous; isolate-skip does not apply.
- Unmatched BBS (would isolate): d5cecc53 BAL@NYY 23:05Z live; 028b8776 CHC@BOS 21:30Z live. Official missing BBS: 824706 CHC@BOS 21:35Z Live; 823489 BAL@NYY 20:10Z Live.
- Last SUCCESS daily: 36156690593 dispatch 15:48:47Z-15:57:53Z (~9.1m) run 765 artifacts ks1-daily-36156690593 + ks1-nightly-36156690593.
- Last SUCCESS schedule: 36143235341 13:47:41Z-14:04:33Z (~16.9m) run 763. Age since latest schedule start ~100m; last attempt 23:20Z (~40m). HEALTH=OK (no CRON_GAP). Watchdog not started. PR 712 not activated. Did not dispatch mlb-research-ingestion.yml.
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers.
- PR 710 open ready; SCHEMA/publish adds p_lgb, pick_status, selection_reason. PR 711 draft shadow-only. PR 712 merged closed; watchdog not activated. PR 713 draft isolate notes only (dirty vs main).
- Last published slate 36156690593 date=2026-09-25: 16 rows, 1 confirmed / 15 projected. official_games=17. bbs_matched=15. exclusions missing_bbs_identity: 823489 retained_previous, 824706. Sits: NYY/NYM/CWS fights (823489+823491 BAL@NYY; 822681 NYM@WSH; 824544 COL@CWS).
- Nightly 36200640894: new_grades=1 ledger_rows=186 locked_rows=192. Official Brier=0.235321 n=186. ACCURACY available. Excluded no_bound_final: 823489, 823409, 824220, 822681, 822760, 823816. New grade includes 824703 lock p_home=0.548.
- Isolate already committed on main. 2stack walk-forward allowed on PR 711 only; not run this hour (duplicate-map is hard error, not isolate work). No official train.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
