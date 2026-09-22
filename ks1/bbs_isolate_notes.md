# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-22 16:13 EDT / 2026-09-22 20:13 UTC:
- Isolate-skip already on main `ks1/daily.py` (`isolate_unmatched=True`). Tests: `tests/ks1_phase5/test_bbs_identity_isolation.py`.
- This branch `ks1/daily.py` is placeholder `SEE_FILE` only. Do not merge this branch; do not patch main; do not copy this placeholder over main.
- Latest schedule: 35764874408 SUCCESS 18:05:17Z-18:15:18Z (~10.0m). Artifacts ks1-daily / ks1-nightly / ks1-loss-patterns / mlb-research-ingestion.
- Latest completed attempt: 35771536258 SUCCESS push 19:05:42Z-19:13:05Z (~7.4m). Artifacts ks1-daily-35771536258, ks1-nightly-35771536258.
- In progress: 35778038795 workflow_dispatch started 20:05:53Z. Last attempt <70m. No CRON_GAP. Watchdog not started. PR 712 watchdog not activated. Did not dispatch mlb-research-ingestion.yml.
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers.
- PR 710 open ready; SCHEMA/publish adds p_lgb, pick_status, selection_reason. PR 711 draft shadow-only. PR 712 closed/merged; watchdog not activated. PR 713 draft isolate notes only.
- Daily 35771536258 date=2026-09-22: 15 rows, 3 confirmed / 12 projected (11 projected + 1 projected_missing_starter). Sits: NYY 823543 confirmed + 823494 projected, NYM 822840, CWS 824061. exclusions=[824785 not_scheduled_before_T10]; bbs_identity_exclusions=[]. projected_missing_starter: SD@LAD 823897.
- Nightly 35771536258: official Brier 0.23313 n=143 new_grades=0 locked_rows=144 ledger_rows=143. ACCURACY=UNAVAILABLE for new grades (n=0). calibration_status=deferred_to_next_nightly. status=no_new_final_grades. admission excluded 823543 no_bound_final.
- 2stack research stays on PR 711 only (isolate already on main). No official train.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
