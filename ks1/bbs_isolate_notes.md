# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-24 15:04 EDT / 2026-09-24 19:04 UTC operator cycle:
- Isolate-skip already on main `ks1/daily.py` (`isolate_unmatched=True`). Tests: `tests/ks1_phase5/test_bbs_identity_isolation.py`.
- This branch `ks1/daily.py` remains placeholder only. Do not merge this branch; do not patch main; do not copy this placeholder over main.
- Latest schedule: 36017719787 SUCCESS 15:06:34Z-15:18:26Z (~11.9m) run 735. Prior schedule 35984774036 SUCCESS 10:01:22Z-10:09:14Z (~7.9m) run 729.
- Latest attempt: dispatch 36039121961 SUCCESS 18:08:07Z-18:19:13Z (~11.1m) run 738. Age since last attempt ~56m. HEALTH=OK. Watchdog not started. PR 712 watchdog not activated. Did not dispatch mlb-research-ingestion.yml.
- Artifacts latest completed attempt: ks1-daily-36039121961, ks1-nightly-36039121961, ks1-loss-patterns-36039121961-1, mlb-research-ingestion-36039121961.
- No failed ingest this window. No isolate code change required.
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers.
- PR 710 open ready; SCHEMA/publish adds p_lgb, pick_status, selection_reason. PR 711 draft shadow-only. PR 712 merged closed; watchdog not activated. PR 713 draft isolate notes only.
- Daily 36039121961 date=2026-09-24: 12 rows, 5 confirmed / 7 projected. Sits remain NYY/NYM/CWS fights.
- Nightly 36039121961: status=no_new_final_grades. official Brier=0.234602 n=172 new_grades=0. locked_rows=175 eligible_graded=172 excluded=[].
- Isolate already committed on main; 2stack walk-forward allowed on PR 711 only, not run this hour. No official train.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
