# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-10-03 10:19 EDT / 2026-10-03 14:19 UTC operator cycle:
- HEALTH=CRON_GAP. Latest schedule 37123486886 SUCCESS run 1079 started 2026-10-03T12:37:45Z finished 12:46:43Z (~9.0m) sha adc65c46. Prior schedule 37116386146 SUCCESS 10:25:43Z-10:33:02Z. Age from last attempt start ~102m (>70). 13:17Z tick absent. Did not start 24h watchdog. Did not dispatch mlb-research-ingestion.yml. Did not activate PR 712.
- Latest push on this workflow: 36944477566 SUCCESS run 1020 2026-10-02T00:08:30Z (#1112 Odds API degrade). Latest failure 37077433832 (2026-10-02T23:24:35Z) was the daily refresh step, not a current BBS slate kill; subsequent schedules succeeded. Did not implement isolate-skip. Branch ks1/daily.py remains the SEE_FILE placeholder that would delete the publisher. Did not restore daily.py. Did not patch main.
- Artifacts on 37123486886: ks1-daily-37123486886 id 11273698690 (175369 B), ks1-nightly-37123486886 id 11273548725 (6598480 B), ks1-loss-patterns-37123486886-1 id 11274018830, mlb-research-ingestion-37123486886 id 11273628762.
- PR 710 open non-draft; SCHEMA/publish path still adds p_lgb/pick_status/selection_reason and merges missing locks. PR 711 draft shadow-only. PR 712 merged 2026-09-11; watchdog not activated. PR 713 draft; head still placeholder.
- TODAY SLATE date=2026-10-03 rows=4 confirmed_lineups=0 projected_lineups=4 exclusions=[]. Sits: CWS@CLE 849829, NYY@TB 849835. NYM not on slate. Other projected: ATL@LAD 849828 (projected_missing_starter; home Tarik Skubal / away missing), SDP@MIL 849830. All market_status=unavailable. publication published=true readback_verified=true. model KS1-LGB-326657a4edfd-DP-9abc4f415689.
- Nightly: status=no_new_final_grades published=false new_grades=0 ledger_rows=227. official Brier=0.235618 logloss=0.664471 n=227. Fresh-day locked rows n=0 so ACCURACY=UNAVAILABLE for today; ledger Brier is historical. Did not rewrite p_home/locks/ledgers.
- Did not promote 2stackMLB. Shadow walk-forward not run (branch isolate still a placeholder; main already emits bbs_identity_exclusions).
- Bottleneck: missing :17 cron tick plus destructive isolate placeholder. Next: do not backfill p_home; do not merge 710/713; do not restore daily.py until a real BBS slate kill; do not activate watchdog.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
