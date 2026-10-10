# 2stackMLB shadow cycle 2026-10-04T11:19Z

promoted: false
champion: KS1-LGB+dual-Poisson
trained_official_model: false
ledger_rewritten: false
p_home_rewritten: false

Source: ks1-nightly-37195327182 and ks1-daily-37195327182 (main workflow_dispatch success, run 1108). Read-only. Not a walk-forward refit.

## Official champion metrics
- official Brier: 0.2350617033215114
- logloss: 0.6633460578789974
- n graded locks: 231
- locked_rows admission: 232
- eligible_graded_rows: 231
- new_grades this tick: 0
- pick accuracy: 0.5887445887445888 (136/231)
- calibration_status: deferred_to_next_nightly
- status: no_new_final_grades

Unchanged versus shadow snapshot from run 37188133895. Zero new grades is not a retrain signal.

## Slate taxonomy
- rows: 2, confirmed_lineups: 0, projected_lineups: 2
- 849825 San Diego Padres @ Milwaukee Brewers: projected, both starters probable, market unavailable
- 849823 Atlanta Braves @ Los Angeles Dodgers: projected_missing_starter, market unavailable
- NYY/NYM and CWS not on slate (sits)
- Odds identity empty; do not shadow-attach a market prior this tick

## Ops note (not a model change)
Latest scheduled ingestion was run 1107 (37192102573) at 2026-10-04T09:25:26Z. No schedule event by 11:19Z (>70m). HEALTH=CRON_GAP. Watchdog not activated.
