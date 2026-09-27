# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-27 16:14 EDT / 2026-09-27 20:14 UTC operator cycle:
- Isolate-skip already on main (`ks1/daily.py` isolate_unmatched=True + `tests/ks1_phase5/test_bbs_identity_isolation.py`). This branch `ks1/daily.py` remains placeholder. Do not merge; do not patch main.
- Latest completed main ingest 36344260689 SUCCESS 19:25:00Z-19:34:34Z (~9.6m) run 826 workflow_dispatch. Artifacts: ks1-daily-36344260689, ks1-nightly-36344260689, ks1-loss-patterns-36344260689-1, mlb-research-ingestion-36344260689.
- Latest scheduled main ingest 36340534467 FAILURE 18:24:36Z-18:32:42Z (~8.1m) run 825. Step failed: Refresh KS1 lineups/starters. Artifacts: ks1-input-identities-36340534467, mlb-research-ingestion-36340534467; ks1-daily missing on that run.
- Last attempt start 19:25:00Z. HEALTH=OK. No CRON_GAP. Did not start 24h watchdog. Did not dispatch ingest. Did not activate PR 712 watchdog.
- Live isolate evidence on 826: unmatched BBS e23f9973-bcfc-49e0-9d09-5718a9ed7144 vs official 823408 TB@PHI; slate continued with 15 rows (game retained as projected).
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers. Did not merge PRs.
- PR 710 open ready; SCHEMA/publish adds p_lgb, pick_status, selection_reason. PR 711 draft shadow-only. PR 712 closed/merged 2026-09-11; watchdog not activated. PR 713 draft isolate notes only.
- TODAY SLATE 36344260689 date=2026-09-27: 15 rows, 13 confirmed / 2 projected (823490 BAL@NYY, 823408 TB@PHI). T-10 lock_coverage_rate=1.0 locked_rows=15. Sits: NYM 822679 NYM@WSH, NYY 823490 BAL@NYY, CWS 824542 COL@CWS.
- Nightly 36344260689: status=no_new_final_grades published=false new_grades=0 ledger_rows=213 official Brier=0.237694 n=213. Today's 15 games excluded as no_bound_final. ACCURACY=UNAVAILABLE for new grades.
- Isolate already on main. 2stack walk-forward allowed on PR 711 only; not executed this hour. No official train.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
