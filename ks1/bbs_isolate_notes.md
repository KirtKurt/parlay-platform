# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-26 11:11 EDT / 2026-09-26 15:11 UTC operator cycle:
- Isolate-skip already on main `ks1/daily.py`. Tests: `tests/ks1_phase5/test_bbs_identity_isolation.py`.
- This branch `ks1/daily.py` remains placeholder only. Do not merge this branch; do not patch main; do not copy this placeholder over main.
- Latest schedule 36244011339 SUCCESS 13:05:40Z-13:12:04Z (~6.4m) run 789. Artifacts: ks1-daily-36244011339, ks1-nightly-36244011339, ks1-loss-patterns-36244011339-1, mlb-research-ingestion-36244011339.
- Prior schedule 36227547754 SUCCESS 07:41:29Z-07:53:54Z run 783 (ks1-daily/nightly present).
- Failed schedules earlier: 36208880797 01:34Z run 776; 36196228493 22:20Z 09-25 run 772; 36174542396 18:36Z 09-25 run 768. Latest run succeeded; no isolate patch this hour.
- HEALTH=CRON_GAP (last attempt 13:05:40Z, ~126m before 15:11Z). Watchdog not started. PR 712 not activated. Did not dispatch mlb-research-ingestion.yml.
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers.
- PR 710 open ready; SCHEMA/publish adds p_lgb, pick_status, selection_reason. PR 711 draft shadow-only. PR 712 merged closed; watchdog not activated. PR 713 draft isolate notes only (dirty vs main).
- TODAY SLATE 36244011339 date=2026-09-26: 13 rows, 0 confirmed / 13 projected (2 projected_missing_starter: 823407 PHI-TB, 823245 SD-ARI). Sits: NYM 822678, CWS 824543. NYY absent.
- Nightly 36244011339: official Brier=0.235854 n=200 new_grades=0 locked_rows=200 ledger_rows=200. ACCURACY available.
- Isolate already committed on main. 2stack walk-forward allowed on PR 711 only; not run this hour (CRON_GAP). No official train.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
