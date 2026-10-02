# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-10-02 02:19 EDT / 2026-10-02 06:19 UTC operator cycle:
- Isolate-skip already on main (`isolate_unmatched=True`, `missing_bbs_identity` in ks1/daily.py + tests/ks1_phase5/test_bbs_identity_isolation.py). This branch `ks1/daily.py` remains a placeholder that would delete the publisher. Do not merge. Do not patch main.
- HEALTH=OK, not CRON_GAP. Latest schedule 36969143101 SUCCESS run 1028 05:29:16Z-05:38:21Z (~9.1m). Prior schedule 36965014872 SUCCESS 04:32:42Z-04:39:51Z. Latest push 36944477566 SUCCESS (#1112 Odds API degrade) 00:08:30Z-00:18:45Z. Last schedule age ~50m. Did not start 24h watchdog. Did not dispatch ingest. Did not activate PR 712 watchdog.
- Artifacts on 36969143101: ks1-daily-36969143101, ks1-nightly-36969143101, ks1-loss-patterns-36969143101-1, mlb-research-ingestion-36969143101.
- No failed run this tick. Live BBS identity did not kill the slate. crosswalk bbs_identity_exclusions=[].
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers. Did not merge PRs. Shadow taxonomy note only on branch stack2mlb-shadow-taxonomy-20261002. No official train.
- PR 710 open non-draft; SCHEMA/publish path still adds p_lgb/pick_status/selection_reason. PR 711 draft shadow-only, updated 2026-10-02T05:17:53Z. PR 712 merged closed; watchdog not activated.
- TODAY SLATE: date=2026-10-02 predictions.parquet rows=0 (travel day; next official games Oct 3 DS). confirmed=0 projected=0. NYY and CWS remain sits. publication readback_verified=true write_keys=[].
- Nightly 36969143101: status=not_due_or_already_completed published=false new_grades=0 ledger locked_rows=228 eligible=227 excluded 823490 no_bound_final. official Brier=0.235618 logloss=0.664471 n=227. as_of ledger 2026-10-01T06:11:45Z.
- Bottleneck: Oct 2 off-day empty publish; 849844 prior hole stays unbackfilled. Next: do not backfill p_home; do not merge 710/713; shadow research only.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
