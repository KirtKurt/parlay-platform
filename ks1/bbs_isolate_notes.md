# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-10-03 08:24 EDT / 2026-10-03 12:24 UTC operator cycle:
- HEALTH=CRON_GAP. Latest schedule 37116386146 SUCCESS run 1076 started 2026-10-03T10:25:43Z finished 10:33:02Z (~7.3m) sha b9f27a90. Prior schedule 37113132414 SUCCESS 09:27:52Z-09:35:13Z. No in-progress run. Expected :17 ticks at 11:17Z and 12:17Z absent; age from last attempt start ~119m (>70). Did not start 24h watchdog. Did not dispatch mlb-research-ingestion.yml. Did not activate PR 712.
- Did not implement isolate-skip this cycle. Latest success has exclusions=[] and bbs_matched_games=4. Last failure 37077433832 (2026-10-02T23:24:35Z) was lineup-refresh provider capture / BBS HTTP 429, not unmatched/ambiguous BBS. Branch ks1/daily.py remains an 8-byte placeholder that would delete the publisher. Did not restore daily.py. Did not patch main.
- Artifacts on 37116386146: ks1-daily-37116386146 id 11271447503 (175098 B), ks1-nightly-37116386146 id 11272230400 (6598530 B), ks1-loss-patterns-37116386146-1 id 11271467472, mlb-research-ingestion-37116386146 id 11271596972.
- PR 710 open non-draft; SCHEMA/publish path still adds p_lgb/pick_status/selection_reason and merges missing locks. PR 711 draft shadow-only. PR 712 merged 2026-09-11; watchdog not activated. PR 713 draft; head still placeholder.
- TODAY SLATE date=2026-10-03 rows=4 confirmed_lineups=0 projected_lineups=4. Sits: CWS@CLE 849829, NYY@TB 849835. Other projected: ATL@LAD 849828 (projected_missing_starter; home name Tarik Skubal / away missing), SDP@MIL 849830. All market_status=unavailable. publication published=true parquet_readback_verified=true. model KS1-LGB-326657a4edfd-DP-9abc4f415689.
- Nightly: status=no_new_final_grades published=false new_grades=0 ledger_rows=227. official Brier=0.235618 logloss=0.664471 n=227. Fresh-day locked rows n=0 so ACCURACY=UNAVAILABLE for today; ledger Brier is historical. Did not rewrite p_home/locks/ledgers.
- Did not promote 2stackMLB. Shadow walk-forward not run (BBS isolate not committed).
- Bottleneck: missed hourly cron plus destructive isolate placeholder. Next: do not backfill p_home; do not merge 710/713; do not restore daily.py until a real BBS slate kill; do not activate watchdog.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
