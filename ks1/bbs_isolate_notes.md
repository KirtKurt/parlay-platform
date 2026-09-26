# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-26 13:06 EDT / 2026-09-26 17:06 UTC operator cycle:
- Isolate-skip already on main (`tests/ks1_phase5/test_bbs_identity_isolation.py`). This branch `ks1/daily.py` is placeholder. Do not merge; do not patch main.
- Latest run 36254334763 SUCCESS dispatch 16:07:36Z-16:18:06Z (~10.5m) run 792. Artifacts: ks1-daily-36254334763, ks1-nightly-36254334763, ks1-loss-patterns-36254334763-1, mlb-research-ingestion-36254334763.
- Latest schedule 36244011339 SUCCESS 13:05:40Z-13:12:04Z run 789. Prior schedule 36227547754 SUCCESS 07:41:29Z run 783.
- Last attempt 16:07Z; ~59m at check. HEALTH=OK. No watchdog. Did not dispatch ingest. Did not activate PR 712 watchdog.
- Failed earlier today: 36215286083 dispatch 03:35Z run 778; later runs succeeded. No isolate patch this hour.
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers. Did not merge PRs.
- PR 710 open ready; SCHEMA/publish adds p_lgb, pick_status, selection_reason. PR 711 draft shadow-only. PR 712 merged 2026-09-11; watchdog not activated. PR 713 draft isolate notes only.
- TODAY SLATE 36254334763 date=2026-09-26: 13 rows, 3 confirmed / 10 projected (2 projected_missing_starter: 823407 PHI-TB, 823245 SD-ARI). Sits: NYM 822678, CWS 824543. NYY absent.
- Nightly 36254334763: official Brier=0.235854 n=200 new_grades=0 locked_rows=200 ledger_rows=200. ACCURACY available.
- Isolate already on main. 2stack walk-forward allowed on PR 711 only; not executed this hour. No official train.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
