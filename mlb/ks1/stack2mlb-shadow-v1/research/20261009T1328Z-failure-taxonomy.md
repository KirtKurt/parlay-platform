# 2stackMLB failure taxonomy — 2026-10-09T13:28Z

promoted: false
champion: KS1-LGB+dual-Poisson
trained_official_model: false

Source: main schedule run 37931474066 (success, 2026-10-09T12:39:28Z–12:47:23Z, ~8m). Artifacts ks1-daily-37931474066, ks1-nightly-37931474066. Not a ledger rewrite.

## Official champion (read-only)
- Brier 0.2367980082815068, logloss 0.6668646297093749, n=241
- new_grades=0, status=no_new_final_grades, locked_rows=242, eligible_graded_rows=241
- exclusion: 823490 no_bound_final (not rewritten)
- pick accuracy 0.5809 (140/241), Brier skill vs coin 0.0132

## Slate
- date 2026-10-09 rows=0, confirmed=0, projected=0, official_games=0
- unmatched BBS event e53688da isolated (unmatched_bbs_identity); slate did not fail
- NYY/NYM and CWS not on slate (sits)
- predictions.parquet present, header only

## Taxonomy
- Zero new grades is not a miss and is not a retrain.
- Empty postseason/off slate is not a BBS kill. Isolate-skip already on main.
- Prior main failure 37859495440 (2026-10-08T23:27Z) died in daily lineup refresh, not identity. 429 degrade stays on existing draft PRs (1125 latest). Do not open another copy. Do not patch main.
- Do not promote 2stackMLB. Do not activate PR 712 watchdog.
