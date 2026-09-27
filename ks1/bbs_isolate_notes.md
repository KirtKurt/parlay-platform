# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-27 12:14 EDT / 2026-09-27 16:14 UTC operator cycle:
- Isolate-skip already on main (`ks1/daily.py` isolate_unmatched=True + `tests/ks1_phase5/test_bbs_identity_isolation.py`). This branch `ks1/daily.py` remains placeholder. Do not merge; do not patch main.
- Latest completed main ingest 36331591887 SUCCESS 16:00:09Z-16:07:37Z (~7.5m) run 822 workflow_dispatch. Artifacts: ks1-daily-36331591887, ks1-nightly-36331591887, ks1-loss-patterns-36331591887-1, mlb-research-ingestion-36331591887.
- Latest scheduled main ingest 36324261985 SUCCESS 13:58:48Z-14:05:05Z (~6.3m) run 820. Artifacts: ks1-daily-36324261985, ks1-nightly-36324261985, ks1-loss-patterns-36324261985-1, mlb-research-ingestion-36324261985.
- Last attempt start 16:00:09Z. HEALTH=OK. No CRON_GAP. Did not start 24h watchdog. Did not dispatch ingest. Did not activate PR 712 watchdog.
- Latest completed runs succeeded; no isolate code patch this hour.
- Live isolate evidence on 822: unmatched BBS e23f9973-bcfc-49e0-9d09-5718a9ed7144; official 823408 TB@PHI exclusion missing_bbs_identity retained_previous=true; slate continued.
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers. Did not merge PRs.
- PR 710 open ready; SCHEMA/publish adds p_lgb, pick_status, selection_reason. PR 711 draft shadow-only. PR 712 closed/merged 2026-09-11; watchdog not activated. PR 713 draft isolate notes only.
- TODAY SLATE 36331591887 date=2026-09-27: 15 rows, 4 confirmed / 11 projected (9 projected + 2 projected_missing_starter: 823164 LAD@SF, 824705 CHC@BOS). T-10 locks 0; all 15 future_before_t10. Sits: NYM 822679 NYM@WSH, NYY 823490 BAL@NYY, CWS 824542 COL@CWS.
- Nightly 36331591887: status=no_new_final_grades published=false new_grades=0 ledger_rows=213 official Brier=0.237694 n=213. locked_rows today=0. ACCURACY=UNAVAILABLE.
- Isolate already on main. 2stack walk-forward allowed on PR 711 only; not executed this hour. No official train.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
