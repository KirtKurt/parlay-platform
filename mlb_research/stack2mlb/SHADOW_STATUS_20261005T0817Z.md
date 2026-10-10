# 2stackMLB shadow status 2026-10-05T08:17Z

promoted: false
trained: false
champion: KS1-LGB+dual-Poisson

Source: ks1-nightly-37280026517 (schedule run 1139, success).
Official Brier 0.23532102537089594, logloss 0.6638706764198633, n=233 locked graded rows.
new_grades this tick: 0. status: no_new_final_grades. calibration_fitted: false.
Prior taxonomy file cited new_grades_2026_10_05=1 from run 37265041423; this later tick added none.

Expanding holdout from shadow_taxonomy_20261005.json stands (no refit):
- n80 hold40 brier 0.218
- n120 hold40 brier 0.2402
- n160 hold40 brier 0.2342
- n200 hold33 brier 0.2321
Worst band remains [0.4, 0.5) (home win rate 0.58 vs mean p 0.46). One miss is not a retrain.

Today slate (ks1-daily-37280026517): 2 projected rows, 0 confirmed. Sits: CWS@CLE 849834, NYY@TB 849839. Market unavailable both. No p_home, lock, or ledger rewrite.
