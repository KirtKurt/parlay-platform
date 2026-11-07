# 2stackMLB shadow cycle 2026-10-04T13:24Z

promoted: false
champion: KS1-LGB+dual-Poisson
attach: shadow-only. No official model train. No p_home, lock, or ledger rewrite.

## Observed
- Latest main schedule run 37199849662 (1110) success 11:46:10Z–11:52:23Z.
- No schedule event for the 12:17Z cron. Dispatch 37203290606 (1111) success 12:46:53Z–12:53:12Z is not a schedule tick.
- HEALTH=CRON_GAP. Watchdog not activated.
- Official ledger from nightly artifact of run 1111: brier 0.2350617033215114, n=231, new_grades=0, locked admission 232 (1 excluded no_bound_final).
- Today slate 2026-10-04: 2 projected rows (MIL/SD, LAD/ATL). NYY/NYM and CWS remain sits (not on slate).

## Shadow action
Walk-forward not refit. Failure taxonomy unchanged: one missing schedule tick is an ops gap, not a retrain signal. Shadow attach remains unpublished.
