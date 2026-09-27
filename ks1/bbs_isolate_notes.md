# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-27 02:14 EDT / 2026-09-27 06:14 UTC operator cycle:
- Isolate-skip already on main (`ks1/daily.py` isolate_unmatched=True + `tests/ks1_phase5/test_bbs_identity_isolation.py`). This branch `ks1/daily.py` remains placeholder `SEE_FILE`. Do not merge; do not patch main.
- Latest main ingest 36297310714 SUCCESS workflow_dispatch 05:28:36Z-05:34:30Z (~5.9m) run 811. Artifacts: ks1-daily-36297310714, ks1-nightly-36297310714, ks1-loss-patterns-36297310714-1, mlb-research-ingestion-36297310714.
- Prior dispatch 36294400871 SUCCESS 04:28Z run 810; 36291474504 SUCCESS 03:27Z run 809; 36288566790 SUCCESS 02:27Z run 808. Latest schedule 36285580019 SUCCESS 01:27:22Z-01:47:33Z (~20.2m) run 807. Prior schedules 36277618787 22:51Z run 800; 36267504541 19:51Z run 797; 36258085016 17:10Z run 794.
- Last attempt 05:28Z; ~46m at check. HEALTH=OK. No CRON_GAP. No watchdog. Did not dispatch ingest. Did not activate PR 712 watchdog.
- No failed latest run. No isolate code patch this hour.
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers. Did not merge PRs.
- PR 710 open ready; SCHEMA/publish adds p_lgb, pick_status, selection_reason. PR 711 draft shadow-only. PR 712 closed/merged 2026-09-11; watchdog not activated. PR 713 draft isolate notes only.
- TODAY SLATE 36297310714 date=2026-09-27: 15 rows, 0 confirmed / 15 projected (13 projected + 2 projected_missing_starter: 823164 LAD@SF, 824705 CHC@BOS). T-10 locks 0; all 15 future_before_t10. Sits: NYM 822679 NYM@WSH, NYY 823490 BAL@NYY, CWS 824542 COL@CWS.
- Nightly 36297310714: status=not_due_or_already_completed published=false. locked_rows=0. ACCURACY=UNAVAILABLE. Prior graded snapshot (run 806 nightly): official Brier=0.235145 n=205 new_grades=0.
- Isolate already on main. 2stack walk-forward allowed on PR 711 only; not executed this hour. No official train.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
