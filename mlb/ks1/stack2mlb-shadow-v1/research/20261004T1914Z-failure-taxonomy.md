# 2stackMLB failure taxonomy — 2026-10-04T19:14Z

Shadow only. promoted=false. No official model trained. No KS1 p_home, lock, or ledger rewrite. No merge.

Source: scheduled run 37221537602 (success, ~6m22s, 2026-10-04T17:42:31Z–17:48:53Z), artifacts ks1-daily-37221537602 and ks1-nightly-37221537602.

## Not a model failure
- HEALTH=CRON_GAP: cron `17 * * * *` slot 18:17Z had not started by 19:14Z. Last attempt start 17:42Z is >70 minutes prior. Do not activate the PR 712 24h watchdog.
- new_grades=0, status=no_new_final_grades. Ledger unchanged (231 graded / 232 locked admitted).
- One admission exclusion: game 823490 reason=no_bound_final. Not a BBS identity kill.
- bbs_identity_exclusions=[] on the daily slate. Main isolate-skip already in force; unmatched BBS did not fail this run.

## Slate taxonomy (date=2026-10-04, n=2)
- 849825 MIL vs SD: confirmed lineups, both starters probable. market_status=unavailable. Pre T-10, not locked.
- 849823 LAD vs ATL: projected, away starter missing. market_status=unavailable. Pre T-10, not locked.
- NYY/NYM and CWS not on this slate (no fight to sit).
- Odds unavailable is a data gap, not a reason to rescore or promote 2stackMLB.

## Official accuracy (unchanged)
- Brier 0.2350617033215114, logloss 0.6633460578789974, n=231.
- Do not promote. Champion remains KS1-LGB+dual-Poisson.
