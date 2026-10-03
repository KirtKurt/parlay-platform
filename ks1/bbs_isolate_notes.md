# KS1 BBS isolate-skip

Unmatched/ambiguous BBS events continue; official games without BBS go to exclusions (`missing_bbs_identity`). Duplicate BBS-to-one-game, schema change, and truncation stay hard errors.

Status 2026-10-03 11:18 EDT / 2026-10-03 15:18 UTC operator cycle:
- HEALTH=OK. Latest schedule 37130249898 SUCCESS run 1081 started 2026-10-03T14:37:14Z finished 14:44:34Z (~7.3m) sha f40271db. Artifacts ks1-daily-37130249898 id 11277085179 (184300 B), ks1-nightly-37130249898 id 11276463590 (6598612 B), ks1-loss-patterns-37130249898-1 id 11276329270, mlb-research-ingestion-37130249898 id 11276129719. Age from last attempt start ~41m (<70). Did not start 24h watchdog. Did not dispatch mlb-research-ingestion.yml. Did not activate PR 712.
- Latest push on this workflow: 36944477566 SUCCESS run 1020 2026-10-02T00:08:30Z (#1112). Latest failure 37077433832 (2026-10-02T23:24:35Z) was BBS 429 provider capture failed in live_inputs, not unmatched BBS identity. Subsequent schedules succeeded. Did not implement isolate-skip. Branch ks1/daily.py remains the SEE_FILE placeholder that would delete the publisher. Did not restore daily.py. Did not patch main.
- PR 710 open non-draft; SCHEMA/publish path still adds p_lgb/pick_status/selection_reason and merges missing locks. PR 711 draft shadow-only. PR 712 merged 2026-09-11; watchdog not activated. PR 713 draft; head still placeholder.
- TODAY SLATE date=2026-10-03 rows=4 confirmed_lineups=1 projected=3 exclusions=[] bbs_identity_exclusions=[]. Sits remain: CWS@CLE 849829 (now confirmed_lineups; still sit), NYY@TB 849835 projected. NYM not on slate. Other: ATL@LAD 849828 projected_missing_starter (home Tarik Skubal / away missing), SDP@MIL 849830 projected. All market_status=unavailable. publication readback_verified=true. model KS1-LGB-326657a4edfd-DP-9abc4f415689.
- Nightly: status=no_new_final_grades published=false new_grades=0 ledger_rows=227. official Brier=0.235618 logloss=0.664471 n=227. Fresh-day locked grades n=0 so ACCURACY=UNAVAILABLE for today; ledger Brier is historical. Did not rewrite p_home/locks/ledgers.
- Did not promote 2stackMLB. Shadow walk-forward not run (branch isolate still a placeholder; main already emits bbs_identity_exclusions).
- Bottleneck: destructive isolate placeholder plus Odds API 401 markets unavailable. Next: do not backfill p_home; do not merge 710/713; do not restore daily.py until a real BBS slate kill; do not activate watchdog.

Do not merge without Kurt approval. Do not rewrite p_home, locks, or ledgers.
