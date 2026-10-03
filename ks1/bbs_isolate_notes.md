# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-10-03 02:20 EDT / 2026-10-03 06:20 UTC operator cycle:
- Do not merge. This branch `ks1/daily.py` is still a one-line placeholder that would delete the publisher. Did not restore daily.py. Did not patch main. Did not implement isolate-skip this cycle: latest run succeeded; last failure was lineup refresh, not unmatched/ambiguous BBS or missing_bbs_identity killing the slate.
- HEALTH=OK, not CRON_GAP. Latest schedule 37100348173 SUCCESS run 1070 05:36:09Z-05:50:21Z (~14.2m) sha 1afd69e. Prior schedule 37097009267 SUCCESS 04:34:47Z-04:45:34Z. Latest push 36944477566 SUCCESS 2026-10-02T00:08:30Z (#1112). Age from last schedule start ~44m. Did not start 24h watchdog. Did not dispatch ingest. Did not activate PR 712 watchdog.
- Artifacts on 37100348173: ks1-daily-37100348173 (174541 B), ks1-nightly-37100348173 (6587578 B), ks1-loss-patterns-37100348173-1, mlb-research-ingestion-37100348173. exclusions=[] bbs_identity_exclusions=[].
- Prior schedule 37077433832 FAILURE run 1060 23:24:35Z-23:31:36Z failed at step Refresh KS1 lineups (nightly grade step succeeded). Not treated as unmatched BBS slate kill. Schedules 1062+ succeeded.
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers. Did not merge PRs. No official train. Shadow walk-forward not run (BBS isolate not committed; new_grades=0).
- PR 710 open non-draft; SCHEMA/publish path still adds p_lgb/pick_status/selection_reason and merges missing locks. PR 711 draft shadow-only. PR 712 merged 2026-09-11; watchdog not activated. PR 713 draft; head still placeholder.
- TODAY SLATE from 37100348173: date=2026-10-03 predictions rows=4 confirmed=0 projected=4 official_games=4. Sits: CWS@CLE 849829, NYY@TB 849835. Other projected: ATL@LAD 849828 (missing starter), SDP@MIL 849830. publication readback_verified=true. model KS1-LGB-326657a4edfd-DP-9abc4f415689.
- Nightly 37100348173: report status=not_due_or_already_completed published=false new_grades=0 ledger_rows=227 locked_rows=228 eligible=227 excluded 823490 no_bound_final. official Brier=0.235618 logloss=0.664471 n=227. ledger as_of 2026-10-02T06:34:10Z. trained_LightGBM=false.
- Bottleneck: isolate branch still a destructive placeholder; 823490 unbound final. Next: do not backfill p_home; do not merge 710/713; do not restore daily.py until a real BBS slate kill.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
