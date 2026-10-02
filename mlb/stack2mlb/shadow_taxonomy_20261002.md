# 2stackMLB shadow failure taxonomy 2026-10-02

promoted=false. No official train. No KS1 p_home, lock, ledger, or SCHEMA write.

Source: ks1-nightly-36969143101 loss_trace (diagnostic only) and committed ledger.

- Official ledger: Brier 0.235618, logloss 0.664471, n=227 graded. new_grades=0 this tick. locked_rows=228. excluded game 823490 no_bound_final.
- Loss-trace sample (as_of 2026-10-01): 70 losses / 109 wins in contribution summary.
- Decision influence on losses: starter 71.9%, team_form 16.0%, market 8.5%, bullpen 3.5%.
- Same groups on wins: starter 66.8%, team_form 15.5%, market 13.9%, bullpen 3.7%. Market influence is weaker on losses than wins; starter remains the dominant miss channel.
- Pattern SELECTED_STARTER_RECENT_DETERIORATION: support 72, loss rate 0.333, lift vs sample -0.058 (not a retrain signal).
- Pattern OPPONENT_STARTER_RECENT_IMPROVEMENT: support 67, loss rate 0.358, lift vs sample -0.033 (not a retrain signal).

Next shadow-only step: chronological walk-forward of Elo/Markov/market-prior vs frozen KS1 p_home on the graded ledger. Do not promote. Do not fit a new official model.
