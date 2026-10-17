# 2stackMLB shadow cycle 2026-10-04T15:18Z

promoted=false. No official model train. No KS1 p_home, lock, ledger, or SCHEMA write.

Source: ks1-nightly-37208394202 committed_ledger (run 37208394202, main).

## Walk-forward (chronological, no refit)
- official graded rows: 231
- official Brier: 0.235062
- logloss: 0.663346
- hit rate: 0.5887 (136/231)
- prior 201 Brier: 0.2364
- last 30 Brier: 0.2261
- 2026-09: n=227 Brier=0.2356
- 2026-10: n=4 Brier=0.2035
- extreme misses (|p-0.5|>=0.15 and wrong side): 11

## Failure taxonomy
One miss is not a retrain. Extreme misses are a shadow label only. Market was unavailable on the 2026-10-04 slate (2 projected rows), so no market-prior attach this tick.

## Attach
Today slate is shadow-only context: 849825 SDP@MIL projected; 849823 ATL@LAD projected_missing_starter. NYY/NYM and CWS not on the official slate.

Champion remains KS1-LGB+dual-Poisson.
