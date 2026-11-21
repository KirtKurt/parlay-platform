# 2stackMLB shadow cycle 2026-10-04T16:18Z

promoted=false. No official model train. No KS1 p_home, lock, ledger, or SCHEMA write.

Source: ks1-nightly-37214372876 (run 37214372876, main, schedule, success). new_grades=0, status=no_new_final_grades.

## Walk-forward (chronological, no refit)
- official graded rows: 231
- official Brier: 0.235062
- logloss: 0.663346
- hit rate: 0.5887 (136/231)
- locked_rows admitted: 232 (1 excluded no_bound_final: 823490)
- today locked prediction rows: 0 (games still pre T-10)

## Failure taxonomy
No new miss this tick. Do not retrain. Market still unavailable on the 2026-10-04 slate, so market-prior attach stays empty.

## Attach
Shadow context only: 849825 SDP@MIL projected (both probable); 849823 ATL@LAD projected_missing_starter. NYY/NYM and CWS not on the official slate. Sit unchanged.

Champion remains KS1-LGB+dual-Poisson. 2stackMLB shadow-only.
