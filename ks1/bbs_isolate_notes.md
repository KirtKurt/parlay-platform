# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-27 14:14 EDT / 2026-09-27 18:14 UTC operator cycle:
- Isolate-skip already on main (`ks1/daily.py` isolate_unmatched=True + `tests/ks1_phase5/test_bbs_identity_isolation.py`). This branch `ks1/daily.py` remains placeholder. Do not merge; do not patch main.
- Latest completed main ingest 36339066823 SUCCESS 18:01:03Z-18:08:50Z (~7.8m) run 824 workflow_dispatch. Artifacts: ks1-daily-36339066823, ks1-nightly-36339066823, ks1-loss-patterns-36339066823-1, mlb-research-ingestion-36339066823.
- Latest scheduled main ingest 36324261985 SUCCESS 13:58:48Z-14:05:05Z (~6.3m) run 820. Artifacts: ks1-daily-36324261985, ks1-nightly-36324261985, ks1-loss-patterns-36324261985-1, mlb-research-ingestion-36324261985.
- Last attempt start 18:01:03Z. HEALTH=OK. No CRON_GAP. Did not start 24h watchdog. Did not dispatch ingest. Did not activate PR 712 watchdog.
- Latest completed runs succeeded; no isolate code patch this hour.
- Live isolate evidence on 824: unmatched BBS e23f9973-bcfc-49e0-9d09-5718a9ed7144; official 823408 TB@PHI exclusion missing_bbs_identity retained_previous=true; slate continued.
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers. Did not merge PRs.
- PR 710 open ready; SCHEMA/publish adds p_lgb, pick_status, selection_reason. PR 711 draft shadow-only. PR 712 closed/merged 2026-09-11; watchdog not activated. PR 713 draft isolate notes only.
- TODAY SLATE 36339066823 date=2026-09-27: 15 rows, 13 confirmed / 2 projected (823490 BAL@NYY, 823408 TB@PHI retained exclusion). T-10 locks 0. Sits: NYM 822679 NYM@WSH, NYY 823490 BAL@NYY, CWS 824542 COL@CWS.
- Nightly 36339066823: status=no_new_final_grades published=false new_grades=0 ledger_rows=213 official Brier=0.237694 n=213. locked_rows today=0. ACCURACY=UNAVAILABLE.
- Isolate already on main. 2stack walk-forward allowed on PR 711 only; not executed this hour. No official train.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
