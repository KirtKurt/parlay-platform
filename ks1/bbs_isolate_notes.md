# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-10-02 12:17 EDT / 2026-10-02 16:17 UTC operator cycle:
- Isolate-skip already on main. This branch `ks1/daily.py` remains a placeholder that would delete the publisher. Do not merge. Do not patch main. Did not restore daily.py this tick.
- HEALTH=OK, not CRON_GAP. Latest schedule 37027340903 SUCCESS run 1046 15:29:31Z-15:35:47Z (~6.3m). Prior schedule 37020440888 SUCCESS 14:31:15Z-14:40:46Z. Last schedule age ~48m. Did not start 24h watchdog. Did not dispatch ingest. Did not activate PR 712 watchdog.
- Artifacts on 37027340903: ks1-daily-37027340903, ks1-nightly-37027340903, ks1-loss-patterns-37027340903-1, mlb-research-ingestion-37027340903. ks1-daily present. No ks1-daily on the old failed 36522116840 (Odds 401, not BBS identity).
- No failed run this tick. Live BBS identity did not kill the slate. exclusions=[].
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers. Did not merge PRs. No official train.
- PR 710 open non-draft; SCHEMA/publish path still adds p_lgb/pick_status/selection_reason and merges missing locks. PR 711 draft shadow-only, updated 2026-10-02T14:24:07Z. PR 712 merged 2026-09-11; watchdog not activated. PR 713 draft; head still placeholder.
- TODAY SLATE: date=2026-10-02 predictions.parquet rows=0. confirmed=0 projected=0. NYY/NYM and CWS remain sits. publication readback_verified=true write_keys=[].
- Nightly 37027340903: status=no_new_final_grades published=false new_grades=0 ledger locked_rows=228 eligible=227 excluded 823490 no_bound_final. official Brier=0.235618 logloss=0.664471 n=227. as_of 2026-10-02T15:32:25Z.
- Bottleneck: Oct 2 empty slate; 823490 unbound final. Next: do not backfill p_home; do not merge 710/713; shadow research only.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
