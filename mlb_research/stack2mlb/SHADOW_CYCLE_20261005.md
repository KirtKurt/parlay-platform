# 2stackMLB shadow cycle 2026-10-05

Champion remains KS1-LGB+dual-Poisson. This note is shadow-only. `promoted=false`. No official model was trained. No p_home, lock, or ledger rewrite.

## Official KS1 snapshot (run 37303153796)
- Nightly official Brier 0.235321, logloss 0.663871, n=233 locked graded rows.
- new_grades=0. Accuracy 137/233 = 0.588. Coin Brier 0.25, skill +0.0147.
- One admitted row excluded from metrics: 823490 no_bound_final.
- Wilson 95% on hit rate still covers 0.5 (0.524-0.649). Not a promotion signal.

## Failure taxonomy (do not retrain from one miss)
- Thin edge vs coin. Calibration still deferred (`calibration_fitted=false`).
- Bound-final hole remains on 823490; not a model defect.
- Today slate is projected-only (NYY and CWS sits). No confirmed lineup rows to score as bets.

## Next shadow work
Walk-forward on the existing graded ledger once a local copy is available. Do not attach shadow outputs to the official publish path.
