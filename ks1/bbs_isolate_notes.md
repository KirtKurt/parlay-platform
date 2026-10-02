# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-10-02 13:14 EDT / 2026-10-02 17:14 UTC operator cycle:
- Isolate-skip already on main (`bbs_assignments(..., isolate_unmatched=True)` path; unmatched continue, official games without BBS become `missing_bbs_identity`). This branch `ks1/daily.py` remains a placeholder that would delete the publisher. Do not merge. Do not patch main. Did not restore daily.py this tick.
- HEALTH=OK, not CRON_GAP. Latest schedule 37034422862 SUCCESS run 1048 16:30:40Z-16:36:58Z (~6.3m). Prior schedule 37027340903 SUCCESS 15:29:31Z-15:35:47Z. Last schedule age ~44m at check. Later dispatch 37035728236 SUCCESS run 1049 16:42:20Z-16:52:40Z (~10.3m). Did not start 24h watchdog. Did not dispatch ingest. Did not activate PR 712 watchdog.
- Artifacts on 37034422862: ks1-daily-37034422862, ks1-nightly-37034422862, ks1-loss-patterns-37034422862-1, mlb-research-ingestion-37034422862. Same four names on dispatch 37035728236. No failed run this tick. Live BBS identity did not kill the slate. exclusions=[] bbs_identity_exclusions=[].
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers. Did not merge PRs. No official train.
- PR 710 open non-draft; SCHEMA/publish path still adds p_lgb/pick_status/selection_reason and merges missing locks. PR 711 draft shadow-only, updated 2026-10-02T14:24:07Z. PR 712 merged 2026-09-11; watchdog not activated. PR 713 draft; head still placeholder.
- TODAY SLATE: date=2026-10-02 predictions.parquet rows=0. confirmed=0 projected=0. official_games=0. NYY/NYM and CWS remain sits. publication readback_verified=true write_keys=[]. model KS1-LGB-326657a4edfd-DP-9abc4f415689.
- Nightly 37035728236: status=no_new_final_grades published=false new_grades=0 ledger_rows=227 locked_rows=228 eligible=227 excluded 823490 no_bound_final. official Brier=0.235618 logloss=0.664471 n=227. as_of 2026-10-02T16:47:18Z.
- Bottleneck: Oct 2 empty official slate; 823490 unbound final. Next: do not backfill p_home; do not merge 710/713; shadow research only.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
