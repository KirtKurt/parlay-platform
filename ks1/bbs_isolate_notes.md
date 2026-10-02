# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-10-02 15:14 EDT / 2026-10-02 19:14 UTC operator cycle:
- Isolate-skip already committed on main (`bbs_assignments(..., isolate_unmatched=True)`). This branch `ks1/daily.py` remains a one-line placeholder that would delete the publisher. Do not merge. Do not patch main. Did not restore daily.py.
- HEALTH=OK, not CRON_GAP. Latest schedule 37048312880 SUCCESS run 1052 18:34:05Z-18:43:22Z (~9.3m). Prior schedule 37040785864 SUCCESS 17:27:04Z-17:36:44Z. Last schedule age ~40m at check. Latest push 36944477566 SUCCESS run 1020 00:08:30Z-00:18:45Z (#1112 Odds 401/403 degrade). Did not start 24h watchdog. Did not dispatch ingest. Did not activate PR 712 watchdog.
- Artifacts on 37048312880: ks1-daily-37048312880, ks1-nightly-37048312880, ks1-loss-patterns-37048312880-1, mlb-research-ingestion-37048312880. No failed schedule this tick. exclusions=[] bbs_identity_exclusions=[]. BBS remaining quota 18.
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers. Did not merge PRs. No official train. Shadow walk-forward not run (no new grades; do not train).
- PR 710 open non-draft; SCHEMA/publish path still adds p_lgb/pick_status/selection_reason and merges missing locks. PR 711 draft shadow-only, updated 18:17Z. PR 712 merged 2026-09-11; watchdog not activated. PR 713 draft; head still placeholder.
- TODAY SLATE from schedule 37048312880: date=2026-10-02 predictions rows=0 confirmed=0 projected=0 official_games=0. NYY/NYM and CWS remain sits. publication readback_verified=true write_keys=[]. model KS1-LGB-326657a4edfd-DP-9abc4f415689.
- Nightly 37048312880: status=no_new_final_grades published=false new_grades=0 ledger_rows=227 locked_rows=228 eligible=227 excluded 823490 no_bound_final. official Brier=0.235618 logloss=0.664471 n=227. as_of 2026-10-02T18:38:34Z. trained_LightGBM=false.
- Bottleneck: Oct 2 empty official slate; 823490 unbound final; BBS quota 18. Next: do not backfill p_home; do not merge 710/713.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
