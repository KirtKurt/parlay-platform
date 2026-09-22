# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-22 00:08 EDT / 2026-09-22 04:08 UTC:
- Isolate-skip already on main `ks1/daily.py` (`isolate_unmatched=True`). Tests: `tests/ks1_phase5/test_bbs_identity_isolation.py`.
- Do not merge this branch; do not patch main. This branch must not replace main `daily.py`.
- Latest schedule: 35676474022 SUCCESS 01:37:56Z-01:46:48Z (~8.9m). Artifacts ks1-daily-35676474022, ks1-nightly-35676474022.
- Prior schedule: 35666821837 SUCCESS 23:16:47Z-23:23:18Z (~6.5m).
- Latest push: 35574570105 SUCCESS 07:46:41Z-07:51:59Z (~5.3m) #1010.
- Last attempt start 01:37Z is >70m with no later schedule → HEALTH=CRON_GAP. Watchdog not started. PR 712 watchdog not activated. Did not dispatch mlb-research-ingestion.yml.
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers.
- PR 710 open ready; SCHEMA/publish adds p_lgb, pick_status, selection_reason. PR 711 draft shadow-only. PR 712 closed/merged; watchdog not activated. PR 713 draft isolate notes.
- Daily 35676474022 date=2026-09-21 as_of=2026-09-22T01:45:52Z: 3 rows, 3 confirmed / 0 projected. Sits: NYY, NYM, CWS (no games on this slate). TOR@BAL 824787 p_home=0.519 confirmed; WSH@DET 824221 p_home=0.594 confirmed; MIN@SF 823169 p_home=0.403 confirmed. exclusions=[]; bbs_identity_exclusions=[].
- Nightly 35676474022: official Brier 0.23227 n=141 new_grades=0 locked_rows=143 ledger_rows=141. Today ACCURACY=UNAVAILABLE (n=0 new locked grades). calibration_status=deferred_to_next_nightly. status=no_new_final_grades.
- 2stack research stays on PR 711 only (isolate already on main). No official train.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
