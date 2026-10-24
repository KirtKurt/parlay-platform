# 2stackMLB shadow snapshot 2026-10-04T14:20Z

promoted: false
champion: KS1-LGB+dual-Poisson
trained_official_model: false

Source: schedule run 37208394202 (success, 2026-10-04T14:12:20Z–14:19:58Z). Artifacts ks1-daily-37208394202, ks1-nightly-37208394202. Not a ledger rewrite.

## Official champion metrics (read-only)
- official Brier: 0.2350617033215114
- logloss: 0.6633460578789974
- n graded locks: 231
- locked_rows admission: 232
- new_grades this tick: 0
- status: no_new_final_grades
- exclusion: 823490 no_bound_final (not rewritten)
- pick accuracy: 0.5887445887445888 (136/231)
- Brier skill vs coin: 0.014938296678488533

## Slate
- date 2026-10-04 rows 2, confirmed_lineups 0, projected 2
- SD@MIL 849825 projected, both starters probable (King/Henderson), market unavailable
- ATL@LAD 849823 projected_missing_starter (Snell probable, away starter missing), market unavailable
- bbs_identity_exclusions: 0 (isolate-skip already on main)
- NYY/NYM and CWS not on slate (sits)

## Failure taxonomy
- No new grade, so no new miss bucket.
- Market prior cannot attach while odds_event_id is empty on both rows.
- Missing away starter is a sit/pass, not a retrain signal.
- Thin official edge is not promotion evidence.

## Gate
Do not promote. Do not train an official model. Do not patch KS1 p_home, locks, or ledgers.
