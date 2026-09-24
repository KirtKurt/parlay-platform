# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-24 02:25 EDT / 2026-09-24 06:25 UTC operator cycle:
- Isolate-skip already on main `ks1/daily.py` (`isolate_unmatched`). Tests: `tests/ks1_phase5/test_bbs_identity_isolation.py`.
- This branch `ks1/daily.py` is placeholder only. Do not merge this branch; do not patch main; do not copy this placeholder over main.
- Latest schedule: 35957395952 SUCCESS 04:51:04Z-04:58:31Z (~7.4m) run 723. Latest attempt: dispatch 35961838461 SUCCESS 05:52:01Z-06:01:33Z (~9.5m) run 724. Age since last attempt ~33m. HEALTH=OK. Watchdog not started. PR 712 watchdog not activated. Did not dispatch mlb-research-ingestion.yml.
- Artifacts latest: ks1-daily-35961838461, ks1-nightly-35961838461, ks1-loss-patterns-35961838461-1, mlb-research-ingestion-35961838461.
- No failed ingest this window. No isolate code change required. bbs_identity_exclusions=[] exclusions=[].
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers.
- PR 710 open ready; SCHEMA/publish adds p_lgb, pick_status, selection_reason. PR 711 draft shadow-only. PR 712 merged closed; watchdog not activated. PR 713 draft isolate notes only.
- Daily 35961838461 date=2026-09-24: 12 rows, 0 confirmed / 12 projected. Sits remain NYY/NYM/CWS fights.
- Nightly 35961838461: status=not_due_or_already_completed. official Brier=0.232673 n=170 new_grades=1. locked_rows=172 excluded 2 no_bound_final (824951, 823894).
- Isolate already committed on main; 2stack walk-forward allowed on PR 711 only, not run this hour. No official train.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
