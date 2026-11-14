# 2stackMLB failure taxonomy — 2026-10-11T11:18Z

promoted: false
champion: KS1-LGB+dual-Poisson
trained_official_model: false

Source: main schedule run 38132029693 (success, 2026-10-11T10:27:47Z–10:36:00Z, ~8m). Artifacts ks1-daily-38132029693, ks1-nightly-38132029693. Not a ledger rewrite.

## Official champion (read-only)
- Brier 0.2367980082815068, logloss 0.6668646297093749, n=241
- new_grades=0, status=no_new_final_grades, locked_rows=242, eligible_graded_rows=241
- exclusion: 823490 no_bound_final (not rewritten)
- pick accuracy 0.5809 (140/241), Brier skill vs coin 0.0132

## Slate
- date 2026-10-11 rows=0, confirmed=0, projected=0, official_games=1
- one exclusion: 849809 missing_bbs_identity (isolate-skip working; slate did not fail)
- bbs_identity_exclusions=[]
- NYY/NYM and CWS not on slate (sits)
- predictions.parquet present, header only

## Taxonomy
- Zero new grades is not a miss and is not a retrain.
- Missing BBS identity correctly excluded; no slate kill.
- Isolate-skip already on main. Do not patch main. Do not merge PR 713.
- Do not promote 2stackMLB. Do not activate PR 712 watchdog.
- Do not train an official model.
