# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-20 01:04 EDT / 2026-09-20 05:04 UTC:
- Isolate-skip already on main `ks1/daily.py` (`isolate_unmatched=True`). Tests: `tests/ks1_phase5/test_bbs_identity_isolation.py`.
- PR 713 draft do-not-merge. Branch notes only.
- Latest schedule: 35480664483 SUCCESS 01:09:44Z-01:22:39Z (~13m). Artifacts ks1-daily-35480664483, ks1-nightly-35480664483.
- Latest push: 35486159481 SUCCESS 03:16:45Z-03:23:40Z. Artifacts ks1-daily-35486159481, ks1-nightly-35486159481.
- Now ~235m after last schedule start (01:09Z). HEALTH=CRON_GAP. Watchdog not started. Did not dispatch mlb-research-ingestion.yml. Did not activate PR 712 watchdog.
- No current BBS slate-kill. No isolate patch this hour.
- PR 710 open ready; SCHEMA/publish adds p_lgb, pick_status, selection_reason. PR 711 draft shadow-only, not promoted. PR 712 merged; watchdog not activated.
- Daily artifact 35486159481: date=2026-09-19 predictions.parquet 15 rows, 15 confirmed / 0 projected. No 2026-09-20 card in artifact. BBS exclusions []. NYY/NYM/CWS sit rule unchanged (Sep 19 card already locked/final).
- Nightly 35486159481: official Brier 0.2362 n=121 new_grades=1 status=completed_catchup locked_rows=125 excluded no_bound_final=4. Calibration deferred.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
