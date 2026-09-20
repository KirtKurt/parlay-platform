# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-20 15:01 EDT / 2026-09-20 19:01 UTC:
- Isolate-skip already on main `ks1/daily.py` (`isolate_unmatched=True`). Tests: `tests/ks1_phase5/test_bbs_identity_isolation.py`.
- Branch notes only. Do not merge. Do not patch main. Do not replace `ks1/daily.py` on this branch.
- Latest schedule: 35523841672 SUCCESS 16:48:02Z-16:56:12Z (~8.2m). Artifacts ks1-daily-35523841672, ks1-nightly-35523841672.
- Latest attempt: 35528737668 workflow_dispatch SUCCESS 18:20:25Z-18:27:50Z (~7.4m). Artifacts ks1-daily-35528737668, ks1-nightly-35528737668. Last attempt <70m; HEALTH=OK. Watchdog not started. Did not dispatch mlb-research-ingestion.yml.
- Failed dispatch 35513338999 13:23:04Z: ingest step `ks1.daily --publish` (not BBS identity). Later runs recovered.
- Did not activate PR 712 watchdog. Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers.
- PR 710 open ready; SCHEMA/publish adds p_lgb, pick_status, selection_reason. PR 711 draft shadow-only. PR 712 merged; watchdog not activated. PR 713 draft isolate notes only.
- Daily 35528737668 date=2026-09-20: 15 rows, 14 confirmed / 1 projected (MIL@BAL). NYY@ARI and DET@CWS sit. PHI@NYM confirmed sit per fight rule.
- Nightly 35528737668: official Brier 0.2353 n=125 new_grades=0 status=no_new_final_grades locked_rows=133 ledger_rows=125. Calibration deferred.
- 2stack research stays on PR 711 only (isolate already on main). No official train.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
