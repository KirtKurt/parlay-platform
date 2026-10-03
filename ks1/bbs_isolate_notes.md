# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-10-03 03:19 EDT / 2026-10-03 07:19 UTC operator cycle:
- Do not merge. This branch `ks1/daily.py` is still a one-line placeholder that would delete the publisher. Did not restore daily.py. Did not patch main. Did not implement isolate-skip this cycle: latest schedule succeeded; last failure 37077433832 was lineup refresh, not unmatched/ambiguous BBS or missing_bbs_identity killing the slate.
- HEALTH=OK, not CRON_GAP. Latest schedule 37104262359 SUCCESS run 1072 06:48:36Z-07:02:04Z (~13.5m) sha 0162934. Prior schedule 37100348173 SUCCESS 05:36:09Z-05:50:21Z. Latest push 36944477566 SUCCESS 2026-10-02T00:08:30Z (#1112). Age from last schedule start ~31m. Did not start 24h watchdog. Did not dispatch ingest. Did not activate PR 712 watchdog.
- Artifacts on 37104262359: ks1-daily-37104262359 (174827 B), ks1-nightly-37104262359 (6598496 B), ks1-loss-patterns-37104262359-1, mlb-research-ingestion-37104262359. exclusions=[] bbs_identity_exclusions=[].
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers. Did not merge PRs. No official train. Shadow walk-forward not run (BBS isolate not committed; new_grades=0).
- PR 710 open non-draft; SCHEMA/publish path still adds p_lgb/pick_status/selection_reason and merges missing locks. PR 711 draft shadow-only. PR 712 merged 2026-09-11; watchdog not activated. PR 713 draft; head still placeholder.
- TODAY SLATE from 37104262359: date=2026-10-03 predictions rows=4 confirmed=0 projected=4 official_games=4. Sits: CWS@CLE 849829, NYY@TB 849835. Other projected: ATL@LAD 849828 (missing starter), SDP@MIL 849830. All market_status=unavailable. publication readback_verified=true. model KS1-LGB-326657a4edfd-DP-9abc4f415689.
- Nightly 37104262359: status=no_new_final_grades published=false new_grades=0 ledger_rows=227 locked_rows=228 eligible=227 excluded 823490 no_bound_final. official Brier=0.235618 logloss=0.664471 n=227. trained_LightGBM=false.
- Bottleneck: isolate branch still a destructive placeholder; 823490 unbound final; slate markets unavailable. Next: do not backfill p_home; do not merge 710/713; do not restore daily.py until a real BBS slate kill.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
