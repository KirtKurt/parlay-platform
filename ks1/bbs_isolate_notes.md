# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-23 04:14 EDT / 2026-09-23 08:14 UTC operator cycle:
- Isolate-skip already on main `ks1/daily.py` (`isolate_unmatched`). Tests: `tests/ks1_phase5/test_bbs_identity_isolation.py`.
- This branch `ks1/daily.py` is placeholder only. Do not merge this branch; do not patch main; do not copy this placeholder over main.
- Latest ingest: 35834681815 DISPATCH SUCCESS 08:00:13Z-08:06:07Z (~5.9m). Last schedule: 35824658252 SUCCESS 05:58:44Z-06:10:29Z (~11.8m). Last attempt 08:00Z (~14m before this cycle). HEALTH=OK (not CRON_GAP; dispatch fills hourly). Watchdog not started. PR 712 watchdog not activated. Did not dispatch mlb-research-ingestion.yml.
- Artifacts latest: ks1-daily-35834681815, ks1-nightly-35834681815, ks1-loss-patterns-35834681815-1, mlb-research-ingestion-35834681815.
- Same-day SUCCESS: 35834681815 08:00Z dispatch, 35829369887 06:59Z dispatch, 35824658252 05:58Z schedule, 35822982332 05:34Z, 35818873869 04:34Z, 35814846250 03:34Z, 35810860279 02:33Z, 35806772148 01:33Z, 35802580507 00:33Z schedule, 35801620711 00:20Z. No failed ingest this window.
- Isolate applied: exclusions=[824710 missing_bbs_identity, 823086 missing_bbs_identity]; bbs_identity_exclusions=[253b8302 unmatched vs 824710, cb1aa5db unmatched vs 823086]. Slate continued (14 / 16 official).
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers.
- PR 710 open ready; SCHEMA/publish adds p_lgb, pick_status, selection_reason. PR 711 draft shadow-only. PR 712 closed/merged; watchdog not activated. PR 713 draft isolate notes only.
- Daily 35834681815 date=2026-09-23: 14 rows, 0 confirmed / 14 projected (11 projected + 3 projected_missing_starter). Sits: NYY 823492, NYM 822841 projected_missing_starter, CWS 824060. Missing starters also TOR@BAL 824785 and 824784.
- Nightly 35834681815: status=no_new_final_grades published=false. official Brier=0.2363 n=158 new_grades=0.
- 2stack research stays on PR 711 only (isolate already on main). No official train. No walk-forward/attach this hour.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
