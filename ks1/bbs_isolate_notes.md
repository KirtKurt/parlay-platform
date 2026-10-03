# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-10-03 04:15 EDT / 2026-10-03 08:15 UTC operator cycle:
- Do not merge. This branch `ks1/daily.py` is still a one-line placeholder that would delete the publisher. Did not restore daily.py. Did not patch main. Did not implement isolate-skip: latest schedule succeeded; last failure 37077433832 lineup refresh raised `provider capture failed` with BBS HTTP 429, not unmatched/ambiguous BBS or missing_bbs_identity killing the slate.
- HEALTH=OK, not CRON_GAP. Latest schedule 37106855070 SUCCESS run 1073 07:35:33Z-07:43:01Z (~7.5m) sha 6f9dca5. Prior schedule 37104262359 SUCCESS 06:48:36Z-07:02:04Z. Latest push 36944477566 SUCCESS 2026-10-02T00:08:30Z (#1112). Age from last schedule start ~40m. Did not start 24h watchdog. Did not dispatch ingest. Did not activate PR 712 watchdog.
- Artifacts on 37106855070: ks1-daily-37106855070 (174650 B), ks1-nightly-37106855070 (6598483 B), ks1-loss-patterns-37106855070-1, mlb-research-ingestion-37106855070. exclusions=[] bbs_identity_exclusions=[] bbs_matched_games=4.
- Did not promote 2stackMLB. Did not rewrite p_home/locks/ledgers. Did not merge PRs. No official train. Shadow walk-forward not run (BBS isolate not committed; new_grades=0).
- PR 710 open non-draft; SCHEMA/publish path still adds p_lgb/pick_status/selection_reason and merges missing locks. PR 711 draft shadow-only. PR 712 merged 2026-09-11; watchdog not activated. PR 713 draft; head still placeholder.
- TODAY SLATE from 37106855070: date=2026-10-03 predictions rows=4 confirmed=0 projected=4 official_games=4. Sits: CWS@CLE 849829, NYY@TB 849835. Other projected: ATL@LAD 849828 (projected_missing_starter; home name Tarik Skubal / away missing), SDP@MIL 849830. All market_status=unavailable. publication readback_verified=true. model KS1-LGB-326657a4edfd-DP-9abc4f415689.
- Nightly 37106855070: status=no_new_final_grades published=false new_grades=0 ledger_rows=227 locked_rows=228 eligible=227 excluded 823490 no_bound_final. official Brier=0.235618 logloss=0.664471 n=227. trained_LightGBM=false.
- Bottleneck: isolate branch still a destructive placeholder; 823490 unbound final; slate markets unavailable; BBS 429 on lineup refresh. Next: do not backfill p_home; do not merge 710/713; do not restore daily.py until a real BBS slate kill.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
