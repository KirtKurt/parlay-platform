# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-19 18:02 EDT / 2026-09-19 18:02 UTC:
- Isolate-skip already on main `ks1/daily.py` (`isolate_unmatched=True`). Tests: `tests/ks1_phase5/test_bbs_identity_isolation.py`.
- PR 713 draft do-not-merge. Branch notes only; daily.py already restored from main.
- Latest schedule: 35459856767 IN_PROGRESS started 18:01:14Z. Prior schedule 35448691942 SUCCESS 14:25-14:31Z (~5m45s). Last completed ingest 35457562305 dispatch SUCCESS 17:16-17:24Z.
- Gap from last schedule attempt 14:25Z to next 18:01Z is cron cadence, not a >70m missed attempt after last start. HEALTH=OK. Watchdog not started. Did not dispatch mlb-research-ingestion.yml.
- Failed schedule 35425130291 05:53Z: abort in `ks1.daily --publish` (exit 134), not unmatched BBS. No isolate patch needed this hour.
- PR 710 open ready; SCHEMA/publish path adds p_lgb, pick_status, selection_reason. PR 711 draft shadow-only. PR 712 merged; watchdog not activated.
- Daily 35457562305: 15 rows, 2 confirmed / 13 projected. DET@CWS confirmed sit. PHI@NYM and NYY@ARI projected sits.
- Nightly 35457562305: official Brier 0.2354 n=110 new_grades=0 status=no_new_final_grades locked_rows=110.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
