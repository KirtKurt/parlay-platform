# 2stackMLB shadow failure taxonomy 2026-10-02

promoted=false. No official train. No KS1 p_home, lock, ledger, or SCHEMA write.

Source: ks1-nightly-36995669166 loss_trace (diagnostic only) and committed ledger metrics. Did not rewrite the ledger.

- Official ledger (nightly report 36995669166, as_of 2026-10-02T10:33:37Z): Brier 0.235618, logloss 0.664471, n=227. new_grades=0. status=no_new_final_grades. published=false. trained_LightGBM=false.
- Loss-trace sample (as_of 2026-10-02T06:34:10Z): 179 non-holdout rows, 70 losses / 109 wins. frozen holdout excluded=48. committed_ledger_rows=227.
- Decision influence on losses: starter 71.9%, team_form 16.0%, market 8.5%, bullpen 3.5%. Market influence remains weaker on losses than wins. Not a retrain signal.

Chronological split of the same 179 settled selected-side probabilities (shadow diagnostic, not a new model):
- Early 2026-09-14 to 2026-09-20: n=89, selected-side Brier 0.228190, wins 56, loss starter influence 72.8%, market 7.6%.
- Late 2026-09-20 to 2026-09-27: n=90, selected-side Brier 0.237397, wins 53, loss starter influence 71.2%, market 9.3%.
- Late window is slightly worse. Starter remains the miss channel. Do not promote. Do not fit Elo/GLM on official labels this tick (graded row payloads are not in the hourly artifact).

Next shadow-only step: attach Elo/Markov/market-prior to frozen KS1 p_home only after graded_ledger rows are read without write. Do not promote.
