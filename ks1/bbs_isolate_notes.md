# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-10-03 14:14 EDT / 2026-10-03 18:14 UTC operator cycle:
- HEALTH=CRON_GAP. Latest schedule 37137566887 SUCCESS run 1084 started 2026-10-03T16:38:03Z finished 16:56:30Z (~18.5m) sha c3131853. No schedule or push run after that. :17 tick for 17:17Z missing. Age from last attempt start ~96m (>70). Artifacts ks1-daily-37137566887 id 11279306517 (154331 B), ks1-nightly-37137566887 id 11278982955 (6608747 B), ks1-loss-patterns-37137566887-1 id 11279162992, mlb-research-ingestion-37137566887 id 11279641195. Did not start 24h watchdog. Did not dispatch mlb-research-ingestion.yml. Did not activate PR 712.
- Latest push on this workflow: 36944477566 SUCCESS 2026-10-02T00:08:30Z (#1112 Odds 401 degrade).
- No new failed run. Prior failure 37077433832 (2026-10-02T23:24:35Z) was BBS 429 in live_inputs, not unmatched BBS identity. Did not implement isolate-skip. Branch ks1/daily.py remains the SEE_FILE placeholder that would delete the publisher. Did not restore daily.py. Did not patch main. Main already calls bbs_assignments(..., isolate_unmatched=True).
- PR 710 open non-draft; SCHEMA/publish path still adds p_lgb/pick_status/selection_reason and merges missing locks. PR 711 draft shadow-only, promoted=false. PR 712 merged 2026-09-11; watchdog not activated. PR 713 draft; head still placeholder.
- TODAY SLATE date=2026-10-03 rows=4 confirmed_lineups=1 projected=3 exclusions=[] bbs_identity_exclusions=[]. Sits remain: CWS@CLE 849829 (confirmed_lineups; still sit), NYY@TB 849835 projected. NYM not on slate. Other: ATL@LAD 849828 projected_missing_starter (home Tarik Skubal / away missing), SDP@MIL 849830 projected. All market_status=unavailable. publication readback_verified=true. model KS1-LGB-326657a4edfd-DP-9abc4f415689.
- Nightly: status=no_new_final_grades published=false new_grades=0 ledger_rows=227 locked_rows=229. official Brier=0.235618 logloss=0.664471 n=227. Fresh-day locked grades n=0 so ACCURACY=UNAVAILABLE for today; ledger Brier is historical. Excluded from admission: 823490 and 849829 no_bound_final. Did not rewrite p_home/locks/ledgers.
- Did not promote 2stackMLB. Shadow walk-forward not run (branch isolate still a placeholder).
- Bottleneck: missing :17 cron plus destructive isolate placeholder and Odds API markets unavailable. Next: do not backfill p_home; do not merge 710/713; do not restore daily.py until a real BBS slate kill; do not activate watchdog.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
