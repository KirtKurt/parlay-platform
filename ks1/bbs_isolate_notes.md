# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-23 19:18 EDT / 2026-09-23 23:18 UTC operator cycle:
- Isolate-skip already on main `ks1/daily.py` (`isolate_unmatched`). Tests: `tests/ks1_phase5/test_bbs_identity_isolation.py`.
- This branch `ks1/daily.py` is placeholder only. Do not merge this branch; do not patch main; do not copy this placeholder over main.
- Latest schedule: 35915474771 SUCCESS 20:22:14Z-20:36:27Z (~14.2m). Latest attempt: 35928050611 DISPATCH SUCCESS 22:23:14Z-22:29:32Z (~6.3m). Prior dispatch 35922058434 SUCCESS 21:22:46Z-21:30:11Z. Last schedule age ~176m. Last attempt age ~55m. HEALTH=CRON_GAP (no schedule event since 20:22Z; hourly coverage is dispatch-only). Watchdog not started. PR 712 watchdog not activated. Did not dispatch mlb-research-ingestion.yml.
- Artifacts latest: ks1-daily-35928050611, ks1-nightly-35928050611, ks1-loss-patterns-35928050611-1, mlb-research-ingestion-35928050611.
- Same-day SUCCESS through 715. No failed ingest this window.
- Isolate applied: exclusions=[824710 missing_bbs_identity, 823086 missing_bbs_identity]. Unmatched BBS 253b8302 / cb1aa5db skipped. Slate continued (14 / 16 official).
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers.
- PR 710 open ready; SCHEMA/publish adds p_lgb, pick_status, selection_reason. PR 711 draft shadow-only. PR 712 closed/merged; watchdog not activated. PR 713 draft isolate notes only.
- Daily 35928050611 date=2026-09-23: 14 rows, 12 confirmed / 2 projected. Sits remain NYY 823492, NYM 822841, CWS 824060.
- Nightly 35928050611: status=no_new_final_grades. official Brier=0.2363 n=160 new_grades=0. locked_rows=162 excluded 823168 and 824784 no_bound_final.
- 2stack research stays on PR 711 only. Isolate already committed on main; no official train. No walk-forward this hour.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
