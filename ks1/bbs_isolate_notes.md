# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-27 17:14 EDT / 2026-09-27 21:14 UTC operator cycle:
- Isolate-skip already on main. This branch `ks1/daily.py` remains placeholder. Do not merge; do not patch main.
- Latest schedule 36340534467 FAILURE 18:24:36Z-18:32:42Z (~8.1m) run 825. Cause: `ValueError: refuse unexpected published prediction removal: 823408` (not BBS isolate). Artifacts: ks1-nightly-36340534467, ks1-input-identities-36340534467; ks1-daily missing.
- Latest completed ingest 36348002021 SUCCESS 20:25:56Z-20:33:10Z (~7.2m) run 827 workflow_dispatch. Artifacts: ks1-daily-36348002021, ks1-nightly-36348002021, ks1-loss-patterns-36348002021-1, mlb-research-ingestion-36348002021.
- Last attempt start 20:25:56Z (~49m). HEALTH=OK. No CRON_GAP. Did not start 24h watchdog. Did not dispatch ingest. Did not activate PR 712 watchdog.
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers. Did not merge PRs.
- PR 710 open; SCHEMA/publish adds p_lgb, pick_status, selection_reason. PR 711 draft shadow-only. PR 712 closed; watchdog off. PR 713 draft notes-only.
- TODAY SLATE 36348002021 date=2026-09-27: 15 rows, 13 confirmed / 2 projected (823490 BAL@NYY, 823408 TB@PHI). Sits: NYM 822679, NYY 823490, CWS 824542.
- Nightly 36348002021: status=no_new_final_grades published=false new_grades=0 ledger_rows=213 official Brier=0.237694 n=213. ACCURACY=UNAVAILABLE for new grades.
- Bottleneck: schedule cadence + 823408 published-row preservation (hard-fail on 825; later dispatch recovered row as projected). Next: branch-only preservation tests; no main patch; no official train.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
