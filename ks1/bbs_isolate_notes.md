# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-23 09:31 EDT / 2026-09-23 13:31 UTC operator cycle:
- Isolate-skip already on main `ks1/daily.py` (`isolate_unmatched`). Tests: `tests/ks1_phase5/test_bbs_identity_isolation.py`.
- This branch `ks1/daily.py` is placeholder only. Do not merge this branch; do not patch main; do not copy this placeholder over main.
- Latest ingest: 35861664790 DISPATCH SUCCESS 12:37:35Z-12:43:50Z (~6.3m). Last schedule: 35855582031 SUCCESS 11:37:08Z-11:43:08Z (~6.0m). Last attempt age ~54m. HEALTH=OK. Watchdog not started. PR 712 watchdog not activated. Did not dispatch mlb-research-ingestion.yml.
- Artifacts latest: ks1-daily-35861664790, ks1-nightly-35861664790, ks1-loss-patterns-35861664790-1, mlb-research-ingestion-35861664790.
- Same-day SUCCESS through 703. No failed ingest this window.
- Isolate applied: exclusions=[824710 missing_bbs_identity, 823086 missing_bbs_identity]. Slate continued (14 / 16 official).
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers.
- PR 710 open ready; SCHEMA/publish adds p_lgb, pick_status, selection_reason. PR 711 draft shadow-only. PR 712 closed/merged; watchdog not activated. PR 713 draft isolate notes only.
- Daily 35861664790 date=2026-09-23: 14 rows, 0 confirmed / 14 projected (11 projected + 3 projected_missing_starter). Sits: NYY 823492, NYM 822841 projected_missing_starter, CWS 824060.
- Nightly 35861664790: status=no_new_final_grades published=false. official Brier=0.2363 n=158 new_grades=0.
- 2stack research stays on PR 711 only. No official train. No walk-forward this hour (all-projected slate).

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
