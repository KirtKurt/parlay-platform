# 2stackMLB shadow cycle 2026-10-04

Champion remains KS1-LGB+dual-Poisson. 2stackMLB stays shadow-only. `promoted=false`.

## Inputs observed, not rewritten
- Nightly artifact run 37199849662: official Brier 0.2350617033215114, n=231, new_grades=0, locked_rows=232, one exclusion `823490` no_bound_final.
- Daily slate 2026-10-04: 2 projected rows (MIL/SD 849825, LAD/ATL 849823). 0 confirmed. NYY/NYM and CWS remain sits (not on slate).
- BBS isolate-skip is already on main (`isolate_unmatched=True`). This cycle does not patch main.

## Shadow research only
- Walk-forward and failure taxonomy stay on this branch. One miss is not a retrain.
- No official LightGBM fit, no KS1 p_home/lock/ledger rewrite, no SCHEMA/publish change, no promotion.
- Market home prob unavailable on both slate rows, so shadow attach cannot score a fresh edge this tick.

## Failure taxonomy (standing)
- Grade hole without bound final stays excluded.
- Thin official edge (pick accuracy 0.589, Brier skill vs coin 0.015) is not promotion evidence.
