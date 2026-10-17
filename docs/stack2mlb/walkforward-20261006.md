# 2stackMLB shadow walk-forward snapshot 2026-10-06

Source: ks1-nightly artifact of run 37400453723 (main). No model trained. No p_home, lock, or ledger rewrite. promoted=false.

Official KS1 graded locks (champion, not challenger):
- Brier 0.23506221616994769
- logloss 0.6633471186010533
- n 234
- new_grades 0
- hits 138 / accuracy 0.5897
- mean_p_home 0.5127 vs home_win_rate 0.5299
- Wilson 95% 0.526-0.651
- skill vs coin Brier +0.0149

Failure taxonomy for this tick: no new finals. Do not treat a zero-grade hour as a retrain signal.

Shadow attach remains blocked from promotion. Next shadow step is chronological disagreement vs KS1 p_home on the same 234 locked rows, still without fitting an official model.
