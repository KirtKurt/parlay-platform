# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-27 08:20 EDT / 2026-09-27 12:20 UTC operator cycle:
- Isolate-skip already on main (`ks1/daily.py` isolate_unmatched=True + `tests/ks1_phase5/test_bbs_identity_isolation.py`). This branch `ks1/daily.py` remains placeholder. Do not merge; do not patch main.
- Latest scheduled main ingest 36305374860 SUCCESS 08:10:37Z-08:16:27Z (~5.8m) run 814. Artifacts: ks1-daily-36305374860, ks1-nightly-36305374860, ks1-loss-patterns-36305374860-1, mlb-research-ingestion-36305374860.
- Latest completed attempt 36318254043 SUCCESS workflow_dispatch 12:13:08Z-12:19:50Z (~6.7m) run 818. Artifacts: ks1-daily-36318254043, ks1-nightly-36318254043, ks1-loss-patterns-36318254043-1, mlb-research-ingestion-36318254043.
- Prior attempts same day: 36314985777 dispatch 11:12Z run 817; 36311770672 dispatch 10:12Z run 816; 36308575960 dispatch 09:11Z run 815; schedule 36305374860 08:10Z run 814.
- Last attempt start 12:13:08Z. HEALTH=OK. No CRON_GAP. Did not start 24h watchdog. Did not dispatch ingest. Did not activate PR 712 watchdog.
- Latest completed runs succeeded; no isolate code patch this hour.
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers. Did not merge PRs.
- PR 710 open ready; SCHEMA/publish adds p_lgb, pick_status, selection_reason. PR 711 draft shadow-only. PR 712 closed/merged 2026-09-11; watchdog not activated. PR 713 draft isolate notes only.
- TODAY SLATE 36318254043 date=2026-09-27: 15 rows, 0 confirmed / 15 projected (13 projected + 2 projected_missing_starter: 823164 LAD@SF, 824705 CHC@BOS). T-10 locks 0; all 15 future_before_t10. Sits: NYM 822679 NYM@WSH, NYY 823490 BAL@NYY, CWS 824542 COL@CWS.
- Nightly 36318254043: status=no_new_final_grades published=false new_grades=0 ledger_rows=213 official Brier=0.237694 n=213. locked_rows today=0. ACCURACY=UNAVAILABLE.
- Isolate already on main. 2stack walk-forward allowed on PR 711 only; not executed this hour. No official train.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
