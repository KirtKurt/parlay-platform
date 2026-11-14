# 2stackMLB shadow status 2026-10-05 03:14 UTC

Champion remains KS1-LGB+dual-Poisson. promoted=false. Do not attach to official publish.

## Baseline copied from official nightly artifact 37256849097
- official Brier 0.23479263956341595, logloss 0.6628014444495455, n=232
- new_grades this tick: 0
- loss_trace non-holdout: 184 rows, 113 wins / 71 losses, loss_rate 0.3859
- locked_rows 234; two excluded for no_bound_final (823490, 849823)

## Not done
- No walk-forward fit this cycle (no graded row replay in this operator sandbox).
- No official model train. No SCHEMA change. No p_home/lock/ledger rewrite.
- Shadow attach stays off the KS1 publish path until a frozen challenger hash exists.

## Next shadow step
Replay graded_ledger chronologically into Elo/market-prior only. One miss is not a retrain.
