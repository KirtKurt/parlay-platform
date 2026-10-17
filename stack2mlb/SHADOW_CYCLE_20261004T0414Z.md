# 2stackMLB shadow cycle 2026-10-04T04:14Z

promoted: false
champion: KS1-LGB+dual-Poisson
attach: shadow-only

## Source
- schedule run 37173290799 (success, main)
- ks1-nightly-37173290799 report.json
- ks1-daily-37173290799 date=2026-10-03 predictions.csv

## Official champion metrics (not a 2stack score)
- brier: 0.23525678394092636
- n: 230 locked graded rows
- new_grades: 0
- status: no_new_final_grades
- locked_rows: 232
- eligible_graded_rows: 230

## Failure taxonomy (one miss is not a retrain)
- no_bound_final: game_id 823490, game_id 849830 (SD@MIL, confirmed lineups, still unbound)
- market_unavailable on all 4 Oct 3 prediction rows; not a model fault
- sits remain: NYY (849835 NYY@TB projected) and CWS (849829 CWS@CLE confirmed but fight sit)

## Not done
- no official model train
- no KS1 SCHEMA or publish-path edit
- no p_home, lock, or ledger rewrite
- no promotion
