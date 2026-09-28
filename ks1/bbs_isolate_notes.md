# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-09-28 01:14 EDT / 2026-09-28 05:14 UTC operator cycle:
- Isolate-skip already on main. This branch `ks1/daily.py` remains placeholder. Do not merge; do not patch main.
- HEALTH=CRON_GAP. Latest schedule 36363449146 SUCCESS run 832 00:46:27Z-00:52:50Z (~6.4m). Next schedule missing >70m after that start (~268m). Did not start 24h watchdog. Did not dispatch ingest. Did not activate PR 712 watchdog.
- Artifacts on 36363449146: ks1-daily-36363449146, ks1-nightly-36363449146, ks1-loss-patterns-36363449146-1, mlb-research-ingestion-36363449146. No ks1-daily missing.
- Last failed schedule 36340534467 FAILURE 18:24:36Z-18:32:42Z (~8.1m) run 825. Hourly refresh failed; later success 36354424657 and 36363449146 recovered. Not a live unmatched-BBS slate killer this cycle.
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers. Did not merge PRs. 2stackMLB research blocked until isolate committed on this branch (placeholder only).
- PR 710 open; SCHEMA/publish path still adds serving-gate fields. PR 711 draft shadow-only. PR 712 closed; watchdog off. PR 713 draft notes-only.
- TODAY SLATE 36363449146 date=2026-09-27: 15 rows, 13 confirmed / 2 projected. Sits: NYM 822679, NYY 823490, CWS 824542. bbs_matched=15 exclusions=[].
- Nightly 36363449146: status=no_new_final_grades published=false new_grades=0 ledger_rows=227 official Brier=0.235618 n=227. ACCURACY available on locked history; admission locked_rows=228 excluded 823490 no_bound_final.
- Bottleneck: schedule cadence gap (~4.5h since last cron). Next: wait next scheduled ingest; keep isolate notes current; no official train.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
