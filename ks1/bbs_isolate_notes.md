# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-22 07:07 EDT / 2026-09-22 11:07 UTC:
- Isolate-skip already on main `ks1/daily.py` (`isolate_unmatched=True`). Tests: `tests/ks1_phase5/test_bbs_identity_isolation.py`.
- Do not merge this branch; do not patch main. This branch must not replace main `daily.py`.
- Latest schedule: 35700982811 SUCCESS 07:42:47Z-07:56:00Z (~13.2m). Artifacts ks1-daily-35700982811, ks1-nightly-35700982811.
- Prior schedule: 35676474022 SUCCESS 01:37:56Z-01:46:48Z (~8.9m).
- Latest push: 35574570105 SUCCESS 07:46:41Z-07:51:59Z (~5.3m) #1010.
- Last attempt start 07:42Z; missed :17 ticks 08:17/09:17/10:17 → HEALTH=CRON_GAP. Watchdog not started. PR 712 watchdog not activated. Did not dispatch mlb-research-ingestion.yml.
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers.
- PR 710 open ready; SCHEMA/publish adds p_lgb, pick_status, selection_reason. PR 711 draft shadow-only. PR 712 closed/merged; watchdog not activated. PR 713 draft isolate notes.
- Daily 35700982811 date=2026-09-22 as_of=2026-09-22T07:55:01Z: 16 rows, 0 confirmed / 16 projected. Sits: NYY (823543, 823494), NYM (822840), CWS (824061). exclusions=[]; bbs_identity_exclusions=[]. Two projected_missing_starter: MIA@CHC 824624, SD@LAD 823897.
- Nightly 35700982811: official Brier 0.23313 n=143 new_grades=0 locked_rows=143 ledger_rows=143. Today ACCURACY=UNAVAILABLE (n=0 new locked grades). calibration_status=deferred_to_next_nightly. status=no_new_final_grades.
- 2stack research stays on PR 711 only (isolate already on main). No official train.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
