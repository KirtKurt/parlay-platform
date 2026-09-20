# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-20 04:13 EDT / 2026-09-20 08:13 UTC:
- Isolate-skip already on main `ks1/daily.py` (`isolate_unmatched=True`). Tests: `tests/ks1_phase5/test_bbs_identity_isolation.py`.
- Branch notes only. Do not merge. Do not patch main.
- Latest schedule: 35493828801 SUCCESS 06:17:07Z-06:24:48Z (~8m). Artifacts ks1-daily-35493828801, ks1-nightly-35493828801.
- Latest successful dispatch: 35496481091 SUCCESS 07:17:45Z-07:23:11Z (~5m). Artifacts ks1-daily-35496481091, ks1-nightly-35496481091.
- Latest push: 35496967044 FAILURE 07:28:38Z-07:34:47Z. Cause: settled-loss trace / loss-patterns capture read — not BBS identity. In-progress push 35497262018 (run_attempt 2) started 08:12:13Z.
- HEALTH=OK. Last schedule 06:17Z; hourly filled by 07:17Z dispatch. Watchdog not started. Did not dispatch mlb-research-ingestion.yml. Did not activate PR 712 watchdog.
- No BBS slate-kill. No isolate code patch this hour.
- PR 710 open ready; SCHEMA/publish adds p_lgb, pick_status, selection_reason. PR 711 draft shadow-only, not promoted. PR 712 merged; watchdog not activated.
- Daily 35496481091 date=2026-09-20: 15 rows, 0 confirmed / 15 projected (2 projected_missing_starter). NYY@ARI and DET@CWS sit. NYM home vs PHI sit.
- Nightly 35496481091: official Brier 0.2353 n=125 new_grades=0 status=no_new_final_grades locked_rows=125. Calibration deferred.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
