# 2stackMLB shadow cycle 2026-10-05T23:15Z

Shadow only. promoted=false. No official model train. No KS1 p_home, lock, or ledger rewrite.

## Observed champion (KS1-LGB+dual-Poisson)
- Nightly artifact run 37382514542 (schedule, failure on daily step): official Brier 0.235321, logloss 0.663871, n=233, new_grades=0, status=no_new_final_grades.
- Pick audit: hits 137/233 (0.588). Not a retrain signal.
- Last successful daily artifact 37325842445 (14:45Z): predictions.parquet rows=2, confirmed_lineups=0, projected_lineups=2. CWS@CLE and NYY@TB. Both sit (projected; NYY and CWS fights remain sits). Market unavailable on those rows.

## Failure taxonomy (ops, not model)
- Class: provider_rate_limit. BBS /v1/matches 429 killed live_inputs on main after nightly publish. Odds 401 already degrades on main.
- Isolate branch ks1-isolate-bbs-identity-20260911 (PR 713, draft) already degrades 429/NETWORK_ERROR to an empty catalogue and isolate-skips missing_bbs_identity. Truncation, schema change, and duplicate BBS-to-one-game stay hard errors.
- Not an identity collision. Not a calibration miss. Do not refit.

## Shadow attach
No new shadow scores this cycle: daily parquet did not refresh after 14:45Z, and odds market is unavailable. Walk-forward harness unchanged. Do not promote.
