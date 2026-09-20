# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-20 19:13 EDT / 2026-09-20 23:13 UTC:
- Isolate-skip already on main `ks1/daily.py` (`isolate_unmatched=True`). Tests: `tests/ks1_phase5/test_bbs_identity_isolation.py`.
- Branch notes only. Do not merge. Do not patch main. Do not replace `ks1/daily.py` on this branch.
- Latest schedule: 35541682299 SUCCESS 22:26:13Z-22:36:39Z (~10.4m). Artifacts ks1-daily-35541682299, ks1-nightly-35541682299.
- Prior schedule: 35532324157 SUCCESS 19:26:47Z-19:32:38Z (~5.9m).
- Latest dispatch: 35538800964 SUCCESS 21:28:18Z-21:38:23Z (~10.1m).
- Last schedule attempt ~47m ago; HEALTH=OK. Watchdog not started. Did not dispatch mlb-research-ingestion.yml.
- Failed dispatch 35513338999 13:23:04Z: ingest `ks1.daily --publish` (not BBS identity). Later runs recovered.
- Did not activate PR 712 watchdog. Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers.
- PR 710 open ready; SCHEMA/publish adds p_lgb, pick_status, selection_reason. PR 711 draft shadow-only. PR 712 merged; watchdog not activated. PR 713 draft isolate notes only.
- Daily 35541682299 date=2026-09-20: 15 rows, 15 confirmed / 0 projected. Sits: PHI@NYM, NYY@ARI, DET@CWS.
- Nightly 35541682299: official Brier 0.2366 n=134 new_grades=3 locked_rows=139 ledger_rows=134. Calibration deferred. 5 no_bound_final exclusions (824546, 823975, 823247, 823896, 825028).
- 2stack research stays on PR 711 only (isolate already on main). No official train.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
