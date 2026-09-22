# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-22 13:21 EDT / 2026-09-22 17:21 UTC:
- Isolate-skip already on main `ks1/daily.py` (`isolate_unmatched=True`). Tests: `tests/ks1_phase5/test_bbs_identity_isolation.py`.
- Do not merge this branch; do not patch main. This branch must not replace main `daily.py`.
- Latest run: 35754187239 SUCCESS workflow_dispatch 16:27:02Z-16:35:42Z (~8.7m). Artifacts ks1-daily-35754187239, ks1-nightly-35754187239, ks1-loss-patterns-35754187239-1.
- Latest schedule: 35733288293 SUCCESS 13:24:46Z-13:35:16Z (~10.5m).
- Prior schedule: 35700982811 SUCCESS 07:42:47Z-07:56:00Z (~13.2m).
- Last attempt start 16:27Z (~54m ago). No CRON_GAP. Watchdog not started. PR 712 watchdog not activated. Did not dispatch mlb-research-ingestion.yml.
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers.
- PR 710 open ready; SCHEMA/publish adds p_lgb, pick_status, selection_reason. PR 711 draft shadow-only. PR 712 closed/merged; watchdog not activated. PR 713 draft isolate notes only.
- Daily 35754187239 date=2026-09-22 as_of=2026-09-22T16:34:37Z: 16 rows, 1 confirmed / 15 projected (14 projected + 1 projected_missing_starter). Sits: NYY (823543 confirmed_lineups, 823494 projected), NYM (822840), CWS (824061). exclusions=[]; bbs_identity_exclusions=[]. projected_missing_starter: SD@LAD 823897 (home starter missing).
- Nightly 35754187239: official Brier 0.23313 n=143 new_grades=0 locked_rows=143 ledger_rows=143. Today ACCURACY=UNAVAILABLE (n=0 new locked grades). calibration_status=deferred_to_next_nightly. status=no_new_final_grades.
- 2stack research stays on PR 711 only (isolate already on main). No official train.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
