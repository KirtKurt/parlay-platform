# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-26 14:08 EDT / 2026-09-26 18:08 UTC operator cycle:
- Isolate-skip already on main (`tests/ks1_phase5/test_bbs_identity_isolation.py`). This branch `ks1/daily.py` is placeholder. Do not merge; do not patch main.
- Latest run 36258085016 SUCCESS schedule 17:10:36Z-17:22:58Z (~12.4m) run 794. Artifacts: ks1-daily-36258085016, ks1-nightly-36258085016, ks1-loss-patterns-36258085016-1, mlb-research-ingestion-36258085016.
- Prior schedule 36244011339 SUCCESS 13:05:40Z run 789; 36227547754 SUCCESS 07:41:29Z run 783.
- Last attempt 17:10Z; ~58m at check. HEALTH=OK. No CRON_GAP. No watchdog. Did not dispatch ingest. Did not activate PR 712 watchdog.
- No failed latest run. No isolate code patch this hour.
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers. Did not merge PRs.
- PR 710 open ready; SCHEMA/publish adds p_lgb, pick_status, selection_reason. PR 711 draft shadow-only. PR 712 closed/merged 2026-09-11; watchdog not activated. PR 713 draft isolate notes only.
- TODAY SLATE 36258085016 date=2026-09-26: 13 rows, 5 confirmed / 8 projected. T-10 locks: 822678 NYM@WSH Live, 824219 PIT@DET Live. Sits: NYM 822678, CWS 824543 COL@CWS. NYY absent.
- Nightly 36258085016: official Brier=0.235854 n=200 new_grades=0 locked_rows=202 ledger_rows=200 excluded no_bound_final=[822678,824219]. ACCURACY available.
- Isolate already on main. 2stack walk-forward allowed on PR 711 only; not executed this hour. No official train.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
