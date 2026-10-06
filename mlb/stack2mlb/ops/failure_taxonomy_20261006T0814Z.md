# 2stackMLB shadow failure taxonomy — 2026-10-06T08:14Z

promoted=false. Champion remains KS1-LGB+dual-Poisson. This note does not train a model, attach to official publish, or rewrite p_home, locks, or ledgers.

## Source
Nightly artifact ks1-nightly-37430780169 (run 37430780169, schedule, success).
official_metrics: brier 0.2353774129257362, logloss 0.6639793672853979, n=235.
new_grades=0. status=no_new_final_grades. locked_rows=236, eligible_graded_rows=235, excluded game 823490 no_bound_final.
pick accuracy 0.5872 (138/235). Mean p_home 0.5124. Home win rate 0.5319.

## Taxonomy (one miss is not a retrain)
- no_new_final: hourly tick had no newly sealed finals. Do not refit.
- unbound_final: 823490 locked but no bound final. Grade hole, not a model miss.
- market_unavailable: 2026-10-06 daily slate (LAD@ATL, MIL@SD) has market_status=unavailable and lineup_status=projected. Shadow attach cannot score market-prior until odds identity returns.
- missing_t10: ingestion PARTIAL, MISSING_T10_SNAPSHOTS on 2026-09-10..13 and later. Historical settlement gap, not a live identity fail.
- favorite_concentration: LAD/MIL/SD dominate picked-win volume. Taxonomy flag only; do not reweight official p_home.

## Walk-forward gate
Do not run a new official walk-forward until a tick has new_grades>0 and market_status available. Shadow harness on PR 711 stays unpromoted.
