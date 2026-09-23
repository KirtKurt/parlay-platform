# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-22 21:11 EDT / 2026-09-23 01:11 UTC operator cycle recorded at 2026-09-23 05:11 UTC:
- Isolate-skip already on main `ks1/daily.py` (`isolate_unmatched`). Tests: `tests/ks1_phase5/test_bbs_identity_isolation.py`.
- This branch `ks1/daily.py` is placeholder only. Do not merge this branch; do not patch main; do not copy this placeholder over main.
- Latest schedule: 35802580507 SUCCESS 00:33:25Z-00:42:33Z (~9.1m). Latest attempt: 35818873869 SUCCESS workflow_dispatch 04:34:19Z-04:42:58Z (~8.6m). Artifacts: ks1-daily-35818873869, ks1-nightly-35818873869, ks1-loss-patterns-35818873869-1, mlb-research-ingestion-35818873869.
- Prior same-day attempts all SUCCESS: 35814846250 03:34Z, 35810860279 02:33Z, 35806772148 01:33Z, 35801620711 00:20Z. No failed ingest this window. HEALTH=OK (last attempt ~37m before 05:11Z). Watchdog not started. PR 712 watchdog not activated. Did not dispatch mlb-research-ingestion.yml.
- Isolate applied on this slate: exclusions=[824710 missing_bbs_identity, 823086 missing_bbs_identity]; bbs_identity_exclusions=[253b8302 unmatched vs 824710, cb1aa5db unmatched vs 823086]. Slate continued (14 scored / 16 official).
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers.
- PR 710 open ready; SCHEMA/publish adds p_lgb, pick_status, selection_reason. PR 711 draft shadow-only. PR 712 closed/merged; watchdog not activated. PR 713 draft isolate notes only.
- Daily 35818873869 date=2026-09-23: 14 rows, 0 confirmed / 14 projected (11 projected + 3 projected_missing_starter). Sits: NYY 823492, NYM 822841 projected_missing_starter, CWS 824060. Missing starters also TOR@BAL 824785 and 824784.
- Nightly 35818873869: status=not_due_or_already_completed published=false. ACCURACY=UNAVAILABLE.
- Research ingest PARTIAL: Statcast COVERAGE_MISMATCH 2026-09-22 expected 12 observed 0; original_settlement MISSING_T10_SNAPSHOTS on 2026-09-10/11. productionAuthorityChanged=false.
- 2stack research stays on PR 711 only (isolate already on main). No official train. No walk-forward/attach this hour.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
