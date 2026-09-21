# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-21 05:08 EDT / 2026-09-21 09:08 UTC:
- Isolate-skip already on main `ks1/daily.py` (`isolate_unmatched=True`). Tests: `tests/ks1_phase5/test_bbs_identity_isolation.py`.
- This branch still stubs `ks1/daily.py`. Do not merge. Do not patch main. Restore `ks1/daily.py` from main before any review.
- Latest any-event run: 35579726553 workflow_dispatch SUCCESS 08:47:15Z-08:56:24Z (~9.1m). Artifacts ks1-daily-35579726553, ks1-nightly-35579726553.
- Latest push: 35574570105 SUCCESS 07:46:41Z-07:51:59Z (~5.3m) #1010.
- Latest schedule: 35568077633 SUCCESS 06:20:55Z-06:26:27Z (~5.5m). Cron `17 * * * *`. No schedule run at 07:17 or 08:17. Last attempt 08:47Z; now 09:08Z (<70m) so HEALTH=OK not CRON_GAP. Watchdog not started. PR 712 watchdog not activated. Did not dispatch mlb-research-ingestion.yml.
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers.
- PR 710 open ready; SCHEMA/publish adds p_lgb, pick_status, selection_reason. PR 711 draft shadow-only. PR 712 closed/merged; watchdog not activated. PR 713 draft isolate notes only (daily.py stub).
- Daily 35579726553 date=2026-09-21 as_of=08:55:29Z: 3 rows, 0 confirmed / 3 projected. Sits: NYY, NYM, CWS (no games on this slate). TOR@BAL 824787 p_home=0.519; WSH@DET 824221 p_home=0.617; MIN@SF 823169 p_home=0.403. bbs_identity_exclusions=[]. lineup_bullpen_profile_status=INVALID_FAIL_CLOSED:LINEUP_BULLPEN_HISTORY_COVERAGE_INCOMPLETE on all 3.
- Nightly 35579726553: official Brier 0.2328 n=140 new_grades=0 locked_rows=140 ledger_rows=140. status=no_new_final_grades. Today ACCURACY=UNAVAILABLE (n=0 locked rows today). calibration_status=deferred_to_next_nightly.
- 2stack research stays on PR 711 only (isolate already on main). No official train.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
