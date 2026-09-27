# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-26 21:14 EDT / 2026-09-27 01:14 UTC operator cycle:
- Isolate-skip already on main (`ks1/daily.py` isolate_unmatched=True + `tests/ks1_phase5/test_bbs_identity_isolation.py`). This branch `ks1/daily.py` remains placeholder `SEE_FILE`. Do not merge; do not patch main.
- Latest main ingest 36283822883 SUCCESS workflow_dispatch 00:52:43Z-00:58:54Z (~6.2m) run 806. Artifacts: ks1-daily-36283822883, ks1-nightly-36283822883, ks1-loss-patterns-36283822883-1, mlb-research-ingestion-36283822883.
- Prior dispatch 36280773381 SUCCESS 23:52:22Z run 805. Latest schedule 36277618787 SUCCESS 22:51:29Z-23:08:02Z (~16.6m) run 800. Prior schedules 36267504541 19:51Z run 797; 36258085016 17:10Z run 794; 36244011339 13:05Z run 789; 36227547754 07:41Z run 783.
- Last attempt 00:52Z; ~22m at check. HEALTH=OK. No CRON_GAP. No watchdog. Did not dispatch ingest. Did not activate PR 712 watchdog.
- No failed latest run. No isolate code patch this hour.
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers. Did not merge PRs.
- PR 710 open ready; SCHEMA/publish adds p_lgb, pick_status, selection_reason. PR 711 draft shadow-only. PR 712 closed/merged 2026-09-11; watchdog not activated. PR 713 draft isolate notes only.
- TODAY SLATE 36283822883 date=2026-09-26: 13 rows, 13 confirmed / 0 projected. T-10 locks 11: 822678 824219 822759 823165 823245 823407 823653 823733 823813 824057 824543. Pre-T10: 823083 LAA@SEA, 824949 HOU@ATH. Sits: NYM 822678 NYM@WSH, CWS 824543 COL@CWS. NYY absent.
- Nightly 36283822883: official Brier=0.235145 n=205 new_grades=0 locked_rows=211 ledger_rows=205 excluded no_bound_final=[823653,823733,824057,824543,823407,823245]. ACCURACY available.
- Isolate already on main. 2stack walk-forward allowed on PR 711 only; not executed this hour. No official train.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
