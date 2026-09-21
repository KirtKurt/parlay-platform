# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-20 23:10 EDT / 2026-09-21 03:10 UTC:
- Isolate-skip already on main `ks1/daily.py` (`isolate_unmatched=True`). Tests: `tests/ks1_phase5/test_bbs_identity_isolation.py`.
- This branch previously stubbed `ks1/daily.py` (436 lines deleted). Do not merge. Do not patch main. Restore `ks1/daily.py` from main before any review; do not treat the stub as the isolate hunk.
- Latest schedule: 35549749830 SUCCESS 01:05:29Z-01:19:56Z (~14.5m). Artifacts ks1-daily-35549749830, ks1-nightly-35549749830.
- Latest completed dispatch: 35553035413 SUCCESS 02:06:18Z-02:13:57Z (~7.6m). Artifacts ks1-daily-35553035413, ks1-nightly-35553035413.
- In-progress dispatch: 35556385612 started 03:06:45Z. Did not dispatch mlb-research-ingestion.yml.
- Prior schedule: 35541682299 SUCCESS 22:26:13Z-22:36:39Z.
- Last schedule attempt ~125m before this note. HEALTH=CRON_GAP. Watchdog not started. PR 712 watchdog not activated.
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers.
- PR 710 open ready; SCHEMA/publish adds p_lgb, pick_status, selection_reason. PR 711 draft shadow-only. PR 712 merged; watchdog not activated. PR 713 draft isolate notes only.
- Daily 35553035413 date=2026-09-20: 15 rows, 15 confirmed / 0 projected. Sits: PHI@NYM, NYY@ARI, DET@CWS.
- Nightly 35553035413: official Brier 0.2340 n=139 new_grades=0 locked_rows=140 ledger_rows=139. status=no_new_final_grades. Calibration deferred. 1 no_bound_final exclusion (824789).
- 2stack research stays on PR 711 only (isolate already on main). No official train.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
