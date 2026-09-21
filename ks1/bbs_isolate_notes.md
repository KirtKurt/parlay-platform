# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-21 18:07 EDT / 2026-09-21 22:07 UTC:
- Isolate-skip already on main `ks1/daily.py` (`isolate_unmatched=True`). Tests: `tests/ks1_phase5/test_bbs_identity_isolation.py`.
- Do not merge this branch; do not patch main.
- Latest schedule: 35644942259 SUCCESS 19:27:35Z-19:33:49Z (~6.2m). Artifacts ks1-daily-35644942259, ks1-nightly-35644942259.
- Prior schedule: 35610220108 SUCCESS 14:09:12Z-14:19:15Z (~10.1m).
- Latest push: 35574570105 SUCCESS 07:46:41Z-07:51:59Z (~5.3m) #1010.
- Last attempt 19:27Z is >70m with no later schedule → HEALTH=CRON_GAP. Watchdog not started. PR 712 watchdog not activated. Did not dispatch mlb-research-ingestion.yml.
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers.
- PR 710 open ready; SCHEMA/publish adds p_lgb, pick_status, selection_reason. PR 711 draft shadow-only. PR 712 closed/merged; watchdog not activated. PR 713 draft isolate notes.
- Daily 35644942259 date=2026-09-21 as_of=19:32:39Z: 3 rows, 1 confirmed / 2 projected. Sits: NYY, NYM, CWS (no games on this slate). TOR@BAL p_home=0.519 projected; WSH@DET p_home=0.594 confirmed_lineups; MIN@SF p_home=0.403 projected. exclusions=[]; bbs_identity_exclusions=[].
- Nightly 35644942259: official Brier 0.23275 n=140 new_grades=0 locked_rows=140 ledger_rows=140. Today ACCURACY=UNAVAILABLE (n=0 new locked grades). calibration_status=deferred_to_next_nightly.
- 2stack research stays on PR 711 only (isolate already on main). No official train.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
