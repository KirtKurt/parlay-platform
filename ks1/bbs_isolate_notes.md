# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-10-01 22:15 EDT / 2026-10-02 02:15 UTC operator cycle:
- Isolate-skip already on main (`isolate_unmatched=True`, `missing_bbs_identity` in ks1/daily.py + tests/ks1_phase5/test_bbs_identity_isolation.py). This branch `ks1/daily.py` remains a placeholder that would delete the publisher. Do not merge. Do not patch main.
- HEALTH=OK, not CRON_GAP. Latest schedule 36952230309 SUCCESS run 1024 01:42:39Z-01:50:05Z (~7.4m). Prior schedule 36949148401 SUCCESS 01:04:19Z-01:12:20Z. Push 36944477566 SUCCESS (#1112 Odds API degrade) 00:08:30Z-00:18:45Z. Last schedule age ~33m. Did not start 24h watchdog. Did not dispatch ingest. Did not activate PR 712 watchdog.
- Artifacts on 36952230309: ks1-daily-36952230309, ks1-nightly-36952230309, ks1-loss-patterns-36952230309-1, mlb-research-ingestion-36952230309.
- Live unmatched BBS did not kill the slate. Report bbs_identity_exclusions: 88cf058e (2026-10-02T02:00Z) and a490caaf (2026-10-02T00:00Z), both unmatched, continued. Official date=2026-10-01 games=1 (849844) matched then excluded `not_scheduled_before_T10` (Live/In Progress).
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers. Did not merge PRs. Shadow snapshot only on branch stack2mlb-shadow-snapshot-20261001. No official train.
- PR 710 open non-draft; SCHEMA/publish path still adds p_lgb/pick_status/selection_reason. PR 711 draft shadow-only. PR 712 merged closed; watchdog not activated. PR 713 draft notes-only; daily.py placeholder. Odds-auth degrade landed as #1112.
- TODAY SLATE: predictions.parquet published, rows=0. Official 1, confirmed_lineups=0, projected_lineups=0. NYY/NYM and CWS remain sits. t10 missing_locked_game_ids=[849844], lock_coverage_rate=0.
- Nightly 36952230309: status=no_new_final_grades published=false new_grades=0 ledger_rows=227 official Brier=0.235618 n=227. Admission locked_rows=228 excluded 823490 no_bound_final. Calibration deferred_to_next_nightly.
- Bottleneck: 849844 went Live with no pre-T10 locked row. Next: do not backfill p_home; do not merge 710/713; shadow research only.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
