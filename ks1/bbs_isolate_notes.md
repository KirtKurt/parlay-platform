# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-21 04:14 EDT / 2026-09-21 08:14 UTC:
- Isolate-skip already on main `ks1/daily.py` (`isolate_unmatched=True`). Tests: `tests/ks1_phase5/test_bbs_identity_isolation.py`.
- This branch still stubs `ks1/daily.py`. Do not merge. Do not patch main. Restore `ks1/daily.py` from main before any review.
- Latest push on main: 35574570105 SUCCESS 07:46:41Z-07:51:59Z (~5.3m) #1010 teardown-exit. Artifacts ks1-daily-35574570105, ks1-nightly-35574570105.
- Latest schedule: 35568077633 SUCCESS 06:20:55Z-06:26:27Z (~5.5m). Artifacts ks1-daily-35568077633, ks1-nightly-35568077633.
- Prior dispatch: 35570893392 SUCCESS 07:00:14Z-07:28:32Z attempt 2.
- Last attempt 07:46Z; now 08:14Z. HEALTH=OK (no 70m gap after last attempt). Watchdog not started. PR 712 watchdog not activated. Did not dispatch mlb-research-ingestion.yml.
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers.
- PR 710 open ready; SCHEMA/publish adds p_lgb, pick_status, selection_reason. PR 711 draft shadow-only. PR 712 merged 2026-09-11; watchdog not activated. PR 713 draft isolate notes only (daily.py stub).
- Daily 35574570105 date=2026-09-21 as_of=07:51:06Z: 3 rows, 0 confirmed / 3 projected. Sits: NYY, NYM, CWS (no games on this slate). TOR@BAL 824787 p_home=0.519; WSH@DET 824221 p_home=0.617; MIN@SF 823169 p_home=0.403. bbs_identity_exclusions=[].
- Nightly 35574570105: official Brier 0.2328 n=140 new_grades=0 locked_rows=140 ledger_rows=140. status=no_new_final_grades. Today ACCURACY=UNAVAILABLE (n=0 locked rows today). calibration_status=deferred_to_next_nightly.
- 2stack research stays on PR 711 only (isolate already on main). No official train.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
