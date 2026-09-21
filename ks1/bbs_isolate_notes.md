# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-21 02:23 EDT / 2026-09-21 06:23 UTC:
- Isolate-skip already on main `ks1/daily.py`. Tests: `tests/ks1_phase5/test_bbs_identity_isolation.py`.
- This branch still stubs `ks1/daily.py`. Do not merge. Do not patch main. Restore `ks1/daily.py` from main before any review.
- Latest schedule: 35568077633 IN_PROGRESS since 06:20:55Z (ingest refresh+nightly done; daily refresh running).
- Latest completed dispatch: 35566519558 SUCCESS 05:58:25Z-06:09:25Z (~11m). Artifacts ks1-daily-35566519558, ks1-nightly-35566519558.
- Prior schedule: 35549749830 SUCCESS 01:05:29Z-01:19:56Z (~14.5m).
- Prior push: 35525520479 SUCCESS 17:19:57Z-17:28:49Z.
- Cron interval recovered: schedule 633 started 06:20Z. HEALTH=OK. Watchdog not started. PR 712 watchdog not activated. Did not dispatch mlb-research-ingestion.yml.
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers.
- PR 710 open ready; SCHEMA/publish adds p_lgb, pick_status, selection_reason. PR 711 draft shadow-only. PR 712 merged; watchdog not activated. PR 713 draft isolate notes only.
- Daily 35566519558 date=2026-09-21: 3 rows, 0 confirmed / 3 projected. Sits: NYY, NYM, CWS (no games on this slate).
- Nightly 35566519558: official Brier 0.2328 n=140 new_grades=0 locked_rows_today=0 ledger_rows=140. status=completed. Today ACCURACY=UNAVAILABLE (n=0 locked rows). Calibration: temp accepted; Platt worse_brier_keep_prior.
- 2stack research stays on PR 711 only (isolate already on main). No official train.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
