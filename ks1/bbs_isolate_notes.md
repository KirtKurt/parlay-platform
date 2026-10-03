# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-10-03 00:15 EDT / 2026-10-03 04:15 UTC operator cycle:
- Do not merge. This branch `ks1/daily.py` is still a one-line placeholder that would delete the publisher. Did not restore daily.py. Did not patch main. Did not implement isolate-skip this cycle: latest failure was not an unmatched/ambiguous BBS slate kill.
- HEALTH=OK, not CRON_GAP. Latest schedule 37094050900 SUCCESS run 1067 03:41:31Z-03:50:29Z (~8.9m) sha c47f95c. Prior schedule 37090823110 SUCCESS 02:44:05Z-02:56:12Z. Latest push 36944477566 SUCCESS 2026-10-02T00:08:30Z (#1112). Age from last schedule start ~34m. Did not start 24h watchdog. Did not dispatch ingest. Did not activate PR 712 watchdog.
- Artifacts on 37094050900: ks1-daily-37094050900 (24099 B), ks1-nightly-37094050900 (6598255 B), ks1-loss-patterns-37094050900-1, mlb-research-ingestion-37094050900. exclusions=[] bbs_identity_exclusions=[].
- Prior schedule 37077433832 FAILURE run 1060 23:24:35Z-23:31:36Z failed at step Refresh KS1 lineups (nightly grade step succeeded). Not treated as unmatched BBS slate kill. Schedules 1062+ succeeded.
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers. Did not merge PRs. No official train. Shadow walk-forward not run (BBS isolate not committed; new_grades=0).
- PR 710 open non-draft; SCHEMA/publish path still adds p_lgb/pick_status/selection_reason and merges missing locks. PR 711 draft shadow-only. PR 712 merged 2026-09-11; watchdog not activated. PR 713 draft; head still placeholder.
- TODAY SLATE from 37094050900: date=2026-10-02 predictions rows=0 confirmed=0 projected=0 official_games=0. NYY/NYM and CWS remain sits. publication readback_verified=true write_keys=[]. model KS1-LGB-326657a4edfd-DP-9abc4f415689.
- Nightly 37094050900: status=no_new_final_grades published=false new_grades=0 ledger_rows=227 locked_rows=228 eligible=227 excluded 823490 no_bound_final. official Brier=0.235618 logloss=0.664471 n=227. as_of 2026-10-03T03:45:51Z. trained_LightGBM=false.
- Bottleneck: Oct 2 empty official slate; 823490 unbound final. Next: do not backfill p_home; do not merge 710/713.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
