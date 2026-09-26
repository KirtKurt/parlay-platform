# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-26 00:04 EDT / 2026-09-26 04:04 UTC operator cycle:
- Isolate-skip already on main `ks1/daily.py`. Tests: `tests/ks1_phase5/test_bbs_identity_isolation.py`.
- This branch `ks1/daily.py` remains placeholder only. Do not merge this branch; do not patch main; do not copy this placeholder over main.
- Latest schedule 36208880797 FAILURE 01:34:25Z-01:44:14Z (~9.8m) run 776. Last attempt 36215286083 dispatch FAILURE 03:35:38Z-03:49:26Z (~13.8m) run 778. No CRON_GAP (last attempt ~28m before 04:04Z).
- Run 778 artifacts: ks1-nightly-36215286083, ks1-input-identities-36215286083, mlb-research-ingestion-36215286083, ks1-loss-patterns-36215286083-1; no ks1-daily (upload warned empty).
- Cause (778 ingest refresh step): `ValueError: multiple BBS IDs map to one official game` in `ks1.daily.bbs_assignments`. Nightly completed_catchup first. Isolate-skip does not apply.
- Collision (kept hard-fail; prior cycle IDs still the working diagnosis): official 824703 CHC@BOS bound by two BBS IDs `e41f7855` and `fceec9f8`.
- Last SUCCESS daily: 36156690593 dispatch 15:48:47Z-15:57:53Z run 765. Last SUCCESS schedule: 36143235341 13:47:41Z-14:04:33Z run 763.
- HEALTH=FAILED_INGEST (cron present). Watchdog not started. PR 712 not activated. Did not dispatch mlb-research-ingestion.yml.
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers.
- PR 710 open ready; SCHEMA/publish adds p_lgb, pick_status, selection_reason. PR 711 draft shadow-only. PR 712 merged closed; watchdog not activated. PR 713 draft isolate notes only (dirty vs main).
- TODAY SLATE: no predictions.parquet (ks1-daily missing). Last published slate 36156690593 date=2026-09-25: 16 rows, 1 confirmed / 15 projected. Sits: NYY/NYM/CWS fights.
- Nightly 36215286083: official Brier=0.235715 n=196 new_grades=2 locked_rows=200 ledger_rows=196. ACCURACY available. Excluded no_bound_final: 823248, 824947, 823085, 823167. catchup_revision=5 published=true.
- Isolate already committed on main. 2stack walk-forward allowed on PR 711 only; not run this hour (duplicate-map is hard error). No official train.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
