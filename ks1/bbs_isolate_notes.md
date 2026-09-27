# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-27 18:14 EDT / 2026-09-27 22:14 UTC operator cycle:
- Isolate-skip already on main. This branch `ks1/daily.py` remains placeholder. Do not merge; do not patch main.
- Latest schedule: 36354424657 IN_PROGRESS run 829 started 22:11:40Z (~3m before this cycle). No CRON_GAP. Did not start 24h watchdog. Did not dispatch ingest. Did not activate PR 712 watchdog.
- Last completed ingest 36351732570 SUCCESS 21:26:24Z-21:44:40Z (~18.3m) run 828 workflow_dispatch. Artifacts: ks1-daily-36351732570, ks1-nightly-36351732570, ks1-loss-patterns-36351732570-1, mlb-research-ingestion-36351732570.
- Last failed schedule 36340534467 FAILURE 18:24:36Z-18:32:42Z (~8.1m) run 825. Cause: `ValueError: refuse unexpected published prediction removal: 823408` (not BBS isolate). Later dispatch recovered 823408 as projected.
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers. Did not merge PRs.
- PR 710 open; SCHEMA/publish adds p_lgb, pick_status, selection_reason. PR 711 draft shadow-only. PR 712 closed; watchdog off. PR 713 draft notes-only.
- TODAY SLATE 36351732570 date=2026-09-27: 15 rows, 13 confirmed / 2 projected (823490 BAL@NYY, 823408 TB@PHI). Sits: NYM 822679, NYY 823490, CWS 824542.
- Nightly 36351732570: status=completed_catchup published=true new_grades=3 ledger_rows=216 official Brier=0.237472 n=216. ACCURACY=available on locked history; no new locked-row n=0 for tonight's slate yet.
- Bottleneck: schedule vs dispatch mix + 823408 published-row preservation hard-fail. Next: keep isolate notes current; branch-only preservation tests; no official train.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
