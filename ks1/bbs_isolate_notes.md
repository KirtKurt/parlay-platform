# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-24 00:07 EDT / 2026-09-24 04:07 UTC operator cycle:
- Isolate-skip already on main `ks1/daily.py` (`isolate_unmatched`). Tests: `tests/ks1_phase5/test_bbs_identity_isolation.py`.
- This branch `ks1/daily.py` is placeholder only. Do not merge this branch; do not patch main; do not copy this placeholder over main.
- Latest schedule: 35934900838 SUCCESS 23:42:14Z-23:48:20Z (~6.1m) run 717. Latest attempt: dispatch 35952664616 SUCCESS 03:43:52Z-03:57:33Z (~13.7m) run 721. Age since last attempt ~24m. HEALTH=OK. Watchdog not started. PR 712 watchdog not activated. Did not dispatch mlb-research-ingestion.yml.
- Artifacts latest: ks1-daily-35952664616, ks1-nightly-35952664616, ks1-loss-patterns-35952664616-1, mlb-research-ingestion-35952664616.
- No failed ingest this window. No isolate code change required.
- Isolate applied: exclusions=[824710 not_scheduled_before_T10, 823086 not_scheduled_before_T10]. Unmatched BBS 253b8302 / cb1aa5db skipped. Slate continued (14 / 16 official).
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers.
- PR 710 open ready; SCHEMA/publish adds p_lgb, pick_status, selection_reason. PR 711 draft shadow-only. PR 712 merged closed; watchdog not activated. PR 713 draft isolate notes only.
- Daily 35952664616 date=2026-09-23: 14 rows, 13 confirmed / 1 projected. Sits remain NYY 823492, NYM 822841, CWS 824060.
- Nightly 35952664616: status=completed_catchup. official Brier=0.2327 n=170 new_grades=1 (824301). locked_rows=172 excluded 2 no_bound_final (824951, 823894).
- 2stack research stays on PR 711 only. Isolate already committed on main; no official train. No walk-forward this hour.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
