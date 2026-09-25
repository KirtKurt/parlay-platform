# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-25 15:03 EDT / 2026-09-25 19:03 UTC operator cycle:
- Isolate-skip already on main `ks1/daily.py`. Tests: `tests/ks1_phase5/test_bbs_identity_isolation.py`.
- This branch `ks1/daily.py` remains placeholder only. Do not merge this branch; do not patch main; do not copy this placeholder over main.
- Latest schedule 36174542396 FAILURE 18:36:54Z-18:43:19Z (~6.4m) run 768. Nightly + identities uploaded; no ks1-daily. Cause: `ValueError: multiple BBS IDs map to one official game` in `ks1.daily.bbs_assignments` during hourly refresh.
- Collision (kept hard-fail): official 824703 CHC@BOS 2026-09-25T17:05Z bound by two BBS IDs at same kickoff: `e41f7855` status=scheduled and `fceec9f8` status=live. Not unmatched/ambiguous; isolate-skip does not apply.
- Prior dispatch 36169540412 FAILURE 17:49:34Z run 767; 36163193879 FAILURE 16:49:21Z run 766. Last SUCCESS daily: 36156690593 dispatch 15:48:47Z-15:57:53Z (~9.1m) run 765 artifacts ks1-daily-36156690593 + ks1-nightly-36156690593.
- Prior SUCCESS schedule 36143235341 13:47:41Z-14:04:33Z (~16.9m) run 763. Age since latest schedule start ~26m. HEALTH=OK (no CRON_GAP). Watchdog not started. PR 712 not activated. Did not dispatch mlb-research-ingestion.yml.
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers.
- PR 710 open ready; SCHEMA/publish adds p_lgb, pick_status, selection_reason. PR 711 draft shadow-only. PR 712 merged closed; watchdog not activated. PR 713 draft isolate notes only (dirty vs main).
- Last published slate 36156690593 date=2026-09-25: 16 rows, 1 confirmed / 15 projected. official_games=17. bbs_matched=15. unmatched BBS d5cecc53 vs 823491/823489; 028b8776 vs 824703/824706. exclusions missing_bbs_identity: 823489 retained_previous, 824706. Sits: NYY/NYM/CWS fights (823489+823491 BAL@NYY; 822681 NYM@WSH; 824544 COL@CWS).
- Nightly 36174542396: new_grades=0 ledger_rows=184 locked_rows=185. Official Brier=0.235500 n=184. ACCURACY available from ledger; no new grades. Eligible excluded 824703 no_bound_final.
- Isolate already committed on main. 2stack walk-forward allowed on PR 711 only; not run this hour (duplicate-map is hard error, not isolate commit work). No official train.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
