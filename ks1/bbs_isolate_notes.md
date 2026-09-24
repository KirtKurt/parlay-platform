# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-23 22:08 EDT / 2026-09-24 02:08 UTC operator cycle:
- Isolate-skip already on main `ks1/daily.py` (`isolate_unmatched`). Tests: `tests/ks1_phase5/test_bbs_identity_isolation.py`.
- This branch `ks1/daily.py` is placeholder only. Do not merge this branch; do not patch main; do not copy this placeholder over main.
- Latest schedule: 35934900838 SUCCESS 23:42:14Z-23:48:20Z (~6.1m) run 717. Prior schedule 35915474771 SUCCESS 20:22:14Z (~14.2m). Latest push on main: 35771536258 SUCCESS 2026-09-22. Last schedule age ~146m. HEALTH=CRON_GAP. Watchdog not started. PR 712 watchdog not activated. Did not dispatch mlb-research-ingestion.yml.
- Artifacts latest: ks1-daily-35934900838, ks1-nightly-35934900838, ks1-loss-patterns-35934900838-1, mlb-research-ingestion-35934900838.
- No failed ingest this window. No isolate code change required.
- Isolate applied: exclusions=[824710 not_scheduled_before_T10, 823086 missing_bbs_identity]. Unmatched BBS 253b8302 / cb1aa5db skipped. Slate continued (14 / 16 official).
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers.
- PR 710 open ready; SCHEMA/publish adds p_lgb, pick_status, selection_reason. PR 711 draft shadow-only. PR 712 closed; watchdog not activated. PR 713 draft isolate notes only.
- Daily 35934900838 date=2026-09-23: 14 rows, 13 confirmed / 1 projected (824784 BAL/TOR). Sits remain NYY 823492, NYM 822841, CWS 824060.
- Nightly 35934900838: status=no_new_final_grades. official Brier=0.2355 n=161 new_grades=0. locked_rows=168 excluded 7 no_bound_final (824784,823327,823410,823492,824868,824060,824625).
- 2stack research stays on PR 711 only. Isolate already committed on main; no official train. No walk-forward this hour.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
