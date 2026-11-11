# 2stackMLB failure taxonomy — 2026-11-11T14:33Z

promoted: false
champion: KS1-LGB+dual-Poisson
trained_official_model: false
BBS isolate-skip already on main (isolate_unmatched=True).

Source: main schedule run 43037422610 (success, 2026-11-11T14:19:52Z–14:29:29Z, ~10m). Artifacts ks1-daily-43037422610, ks1-nightly-43037422610. Not a ledger rewrite.

## Official champion (read-only)
- Brier 0.23750452432767744, logloss 0.6682938749491157, n=250
- new_grades=0, status=no_new_final_grades, locked_rows=251, eligible_graded_rows=250
- exclusion: 823490 no_bound_final (not rewritten)
- pick accuracy 0.572 (143/250), Brier skill vs coin ~0.0125

## Slate
- date 2026-11-11 rows=0, confirmed=0, projected=0, official_games=0
- predictions.parquet present (header only), bbs_identity_exclusions=[]
- NYY/NYM and CWS not on slate (sits)
- Offseason: empty slate expected, not a BBS kill

## Taxonomy
- Zero new grades is not a miss and is not a retrain.
- Empty offseason slate is not a data problem or model failure.
- Isolate-skip already committed on main; no unmatched BBS killed the slate.
- Do not promote 2stackMLB. Do not activate PR 712 watchdog. Do not rewrite KS1 p_home, locks, or ledgers.
