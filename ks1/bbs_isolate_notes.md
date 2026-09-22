# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-22 14:17 EDT / 2026-09-22 18:17 UTC:
- Isolate-skip already on main `ks1/daily.py` (`isolate_unmatched=True`). Tests: `tests/ks1_phase5/test_bbs_identity_isolation.py`.
- Do not merge this branch; do not patch main. This branch must not replace main `daily.py`.
- Latest run: 35764874408 SUCCESS schedule 18:05:17Z-18:15:18Z (~10.0m). Artifacts ks1-daily-35764874408, ks1-nightly-35764874408, ks1-loss-patterns-35764874408-1, mlb-research-ingestion-35764874408.
- Prior schedule: 35733288293 SUCCESS 13:24:46Z-13:35:16Z (~10.5m).
- Prior schedule: 35700982811 SUCCESS 07:42:47Z-07:56:00Z (~13.2m).
- Last attempt start 18:05Z (~12m ago). No CRON_GAP. Watchdog not started. PR 712 watchdog not activated. Did not dispatch mlb-research-ingestion.yml.
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers.
- PR 710 open ready; SCHEMA/publish adds p_lgb, pick_status, selection_reason. PR 711 draft shadow-only. PR 712 closed/merged; watchdog not activated. PR 713 draft isolate notes only.
- Daily 35764874408 date=2026-09-22 as_of=2026-09-22T18:14:12Z: 15 rows, 1 confirmed / 14 projected (13 projected + 1 projected_missing_starter). Sits: NYY (823543 confirmed_lineups, 823494 projected), NYM (822840), CWS (824061). exclusions=[824785 not_scheduled_before_T10]; bbs_identity_exclusions=[]. projected_missing_starter: SD@LAD 823897 (home starter missing).
- Nightly 35764874408: official Brier 0.23313 n=143 new_grades=0 locked_rows=143 ledger_rows=143. Today ACCURACY=UNAVAILABLE (n=0 new locked grades). calibration_status=deferred_to_next_nightly. status=no_new_final_grades.
- 2stack research stays on PR 711 only (isolate already on main). No official train.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
