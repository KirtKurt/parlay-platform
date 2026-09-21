# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-21 11:40 EDT / 2026-09-21 15:40 UTC:
- Isolate-skip already on main `ks1/daily.py` (`isolate_unmatched=True`). Tests: `tests/ks1_phase5/test_bbs_identity_isolation.py`.
- This branch previously stubbed `ks1/daily.py`. Restored from main this cycle. Do not merge; do not patch main.
- Latest schedule: 35610220108 SUCCESS 14:09:12Z-14:19:15Z (~10.1m). Artifacts ks1-daily-35610220108, ks1-nightly-35610220108.
- Latest dispatch: 35617026085 SUCCESS 15:09:31Z-15:17:58Z (~8.5m). Artifacts ks1-daily-35617026085, ks1-nightly-35617026085.
- Latest push: 35574570105 SUCCESS 07:46:41Z-07:51:59Z (~5.3m) #1010.
- Last attempt 15:09Z is <70m so HEALTH=OK not CRON_GAP. Watchdog not started. PR 712 watchdog not activated. Did not dispatch mlb-research-ingestion.yml.
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers.
- PR 710 open ready; SCHEMA/publish adds p_lgb, pick_status, selection_reason. PR 711 draft shadow-only. PR 712 closed/merged; watchdog not activated. PR 713 draft isolate notes + restored daily.py.
- Daily 35617026085 date=2026-09-21 as_of=15:16:54Z: 3 rows, 0 confirmed / 3 projected. Sits: NYY, NYM, CWS (no games on this slate). TOR@BAL p_home=0.519; WSH@DET p_home=0.594; MIN@SF p_home=0.403. exclusions=[].
- Nightly 35617026085: official Brier 0.23275 n=140 new_grades=0 locked_rows=140 ledger_rows=140. Today ACCURACY=UNAVAILABLE (n=0 new locked grades). calibration_status=deferred_to_next_nightly.
- 2stack research stays on PR 711 only (isolate already on main). No official train.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
