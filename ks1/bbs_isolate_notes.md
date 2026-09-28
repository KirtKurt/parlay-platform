# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-27 20:17 EDT / 2026-09-28 00:17 UTC operator cycle:
- Isolate-skip already on main. This branch `ks1/daily.py` remains placeholder. Do not merge; do not patch main.
- HEALTH=CRON_GAP. Latest schedule 36354424657 SUCCESS run 829 22:11:40Z-22:26:59Z (~15.3m). Next schedule missing >70m after that start (~126m). Did not start 24h watchdog. Did not dispatch ingest. Did not activate PR 712 watchdog.
- Artifacts on 36354424657: ks1-daily-36354424657, ks1-nightly-36354424657, ks1-loss-patterns-36354424657-1, mlb-research-ingestion-36354424657.
- Last failed schedule 36340534467 FAILURE 18:24:36Z-18:32:42Z (~8.1m) run 825. Cause: published prediction removal 823408 (not BBS isolate). Later success recovered 823408 as projected.
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers. Did not merge PRs. 2stackMLB research blocked until isolate committed on this branch (placeholder only).
- PR 710 open; SCHEMA/publish adds p_lgb, pick_status, selection_reason. PR 711 draft shadow-only. PR 712 closed/merged; watchdog off. PR 713 draft notes-only.
- TODAY SLATE 36354424657 date=2026-09-27: 15 rows, 13 confirmed / 2 projected (823490 BAL@NYY, 823408 TB@PHI). Sits: NYM 822679, NYY 823490, CWS 824542.
- Nightly 36354424657: status=completed_catchup published=true new_grades=11 ledger_rows=227 official Brier=0.235618 n=227. ACCURACY=available on locked history; admission locked_rows=228 excluded 823490 no_bound_final.
- Bottleneck: schedule cadence gap + 823408 published-row preservation hard-fail. Next: wait next cron; keep isolate notes current; no official train.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
