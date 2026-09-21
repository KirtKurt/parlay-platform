# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-21 01:07 EDT / 2026-09-21 05:07 UTC:
- Isolate-skip already on main `ks1/daily.py`. Tests: `tests/ks1_phase5/test_bbs_identity_isolation.py`.
- This branch still stubs `ks1/daily.py`. Do not merge. Do not patch main. Restore `ks1/daily.py` from main before any review.
- Latest schedule: 35549749830 SUCCESS 01:05:29Z-01:19:56Z (~14.5m). Artifacts ks1-daily-35549749830, ks1-nightly-35549749830.
- Prior schedule: 35541682299 SUCCESS 22:26:13Z-22:36:39Z.
- Latest push: 35525520479 SUCCESS 17:19:57Z-17:28:49Z.
- Cron `17 * * * *`. Last schedule attempt 01:05Z; missed 02:17/03:17/04:17. HEALTH=CRON_GAP. Watchdog not started. PR 712 watchdog not activated. Did not dispatch mlb-research-ingestion.yml.
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers.
- PR 710 open ready; SCHEMA/publish adds p_lgb, pick_status, selection_reason. PR 711 draft shadow-only. PR 712 merged; watchdog not activated. PR 713 draft isolate notes only.
- Daily 35549749830 date=2026-09-20: 15 rows, 15 confirmed / 0 projected. Sits: NYY, NYM, CWS.
- Nightly 35549749830: official Brier 0.2340 n=139 new_grades=1 locked_rows=140 ledger_rows=139. status=completed_catchup. Calibration deferred. 1 no_bound_final exclusion (824789).
- 2stack research stays on PR 711 only (isolate already on main). No official train.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
