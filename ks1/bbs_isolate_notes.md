# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-23 18:21 EDT / 2026-09-23 22:21 UTC operator cycle:
- Isolate-skip already on main `ks1/daily.py` (`isolate_unmatched`). Tests: `tests/ks1_phase5/test_bbs_identity_isolation.py`.
- This branch `ks1/daily.py` is placeholder only. Do not merge this branch; do not patch main; do not copy this placeholder over main.
- Latest ingest: 35922058434 DISPATCH SUCCESS 21:22:46Z-21:30:11Z (~7.4m). Last schedule: 35915474771 SUCCESS 20:22:14Z-20:36:27Z (~14.2m). Last attempt age ~59m. HEALTH=OK. Watchdog not started. PR 712 watchdog not activated. Did not dispatch mlb-research-ingestion.yml.
- Artifacts latest: ks1-daily-35922058434, ks1-nightly-35922058434, ks1-loss-patterns-35922058434-1, mlb-research-ingestion-35922058434.
- Same-day SUCCESS through 714. No failed ingest this window.
- Isolate applied: exclusions=[824710 missing_bbs_identity, 823086 missing_bbs_identity]. Unmatched BBS 253b8302 / cb1aa5db skipped. Slate continued (14 / 16 official).
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers.
- PR 710 open ready; SCHEMA/publish adds p_lgb, pick_status, selection_reason. PR 711 draft shadow-only. PR 712 closed/merged; watchdog not activated. PR 713 draft isolate notes only.
- Daily 35922058434 date=2026-09-23: 14 rows, 11 confirmed / 3 projected. Sits remain NYY 823492, NYM 822841, CWS 824060.
- Nightly 35922058434: status=no_new_final_grades published=false. official Brier=0.2363 n=160 new_grades=0. locked_rows=161 excluded 823168 no_bound_final (Live).
- 2stack research stays on PR 711 only. Isolate already on main; no official train. No walk-forward this hour.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
