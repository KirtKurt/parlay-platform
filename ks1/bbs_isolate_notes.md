# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-23 17:22 EDT / 2026-09-23 21:22 UTC operator cycle:
- Isolate-skip already on main `ks1/daily.py` (`isolate_unmatched`). Tests: `tests/ks1_phase5/test_bbs_identity_isolation.py`.
- This branch `ks1/daily.py` is placeholder only. Do not merge this branch; do not patch main; do not copy this placeholder over main.
- Latest ingest: 35915474771 SCHEDULE SUCCESS 20:22:14Z-20:36:27Z (~14.2m). Prior schedule: 35892492525 SUCCESS 16:59:01Z-17:06:36Z. Last attempt age ~60m. HEALTH=OK. Watchdog not started. PR 712 watchdog not activated. Did not dispatch mlb-research-ingestion.yml.
- Artifacts latest: ks1-daily-35915474771, ks1-nightly-35915474771, ks1-loss-patterns-35915474771-1, mlb-research-ingestion-35915474771.
- Same-day SUCCESS through 713. No failed ingest this window.
- Isolate applied: exclusions=[824710 missing_bbs_identity, 823086 missing_bbs_identity]. Slate continued (14 / 16 official).
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers.
- PR 710 open ready; SCHEMA/publish adds p_lgb, pick_status, selection_reason. PR 711 draft shadow-only. PR 712 closed/merged; watchdog not activated. PR 713 draft isolate notes only.
- Daily 35915474771 date=2026-09-23: 14 rows, 8 confirmed / 6 projected (5 projected + 1 projected_missing_starter 824784 BAL). Sits: NYY 823492 projected, NYM 822841 confirmed (sit per policy), CWS 824060 confirmed (sit).
- Nightly 35915474771: status=completed_catchup published=true. official Brier=0.2363 n=160 new_grades=1. locked_rows=161 excluded 823168 no_bound_final.
- 2stack research stays on PR 711 only. No official train. No walk-forward this hour.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
