# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-25 01:04 EDT / 2026-09-25 05:04 UTC operator cycle:
- Isolate-skip already on main `ks1/daily.py` (`isolate_unmatched=True`). Tests: `tests/ks1_phase5/test_bbs_identity_isolation.py`.
- This branch `ks1/daily.py` remains placeholder only. Do not merge this branch; do not patch main; do not copy this placeholder over main.
- Latest main ingest 36094860571 SUCCESS workflow_dispatch 04:32:30Z-04:38:39Z (~6.2m) run 751. Artifacts: ks1-daily-36094860571, ks1-nightly-36094860571, ks1-loss-patterns-36094860571-1, mlb-research-ingestion-36094860571.
- Latest schedule: 36082319345 SUCCESS 01:30:32Z-01:51:00Z (~20.5m) run 748. Age since last schedule start ~214m but last attempt was 04:32Z (~32m ago). HEALTH=OK. Watchdog not started. PR 712 watchdog not activated. Did not dispatch mlb-research-ingestion.yml.
- No failed ingest this window. No isolate code change required.
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers.
- PR 710 open ready; SCHEMA/publish adds p_lgb, pick_status, selection_reason. PR 711 draft shadow-only. PR 712 merged closed; watchdog not activated. PR 713 draft isolate notes only (dirty vs main).
- Daily 36094860571 date=2026-09-25: 15 rows, 0 confirmed / 15 projected (9 projected + 6 projected_missing_starter). bbs_matched=15. bbs_identity_exclusions=2 unmatched BBS (d5cecc53 vs 823489/823491; 028b8776 vs 824703/824706). official exclusions missing_bbs_identity: 823491, 824706. Sits remain NYY/NYM/CWS fights.
- Nightly 36094860571: status=not_due_or_already_completed published=false. ACCURACY from this artifact UNAVAILABLE (no new grades). Prior schedule nightly 36082319345: official Brier=0.234047 n=179 new_grades=0.
- Isolate already committed on main; 2stack walk-forward allowed on PR 711 only, not run this hour. No official train.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
