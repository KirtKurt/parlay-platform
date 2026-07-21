# INQSI MLB Signal Audit — 2026-07-21

## Authoritative rolling result

- 11 completed final games
- 2 exact canonical locked predictions graded
- 0 correct, 2 wrong (0.0%)
- 9 missing canonical locked predictions
- 0 playable recommendations
- 5 clean learning rows; 9 quarantined rows

## Losing profiles

1. Minnesota over Cleveland: 54.69%, positive full-window movement, no book agreement, no steam, no run-line confirmation, two aggregate reversals, three 180-minute reversals, seven full-window reversals, and late 15/60-minute direction conflict. Cleveland won 13-4.
2. Texas over the White Sox: 60.16%, selected-side movement negative, no book agreement, no steam, no run-line confirmation, two aggregate reversals, and a probability/direction integrity correction. The White Sox won 10-3.

## Operational integrity

The pull guard reported 92 expected quarter-hour slots, 91 unique slots, one missing slot, and 275 raw rows, including 184 duplicate or extra pulls. Duplicate observations can distort reversal, velocity, acceleration, volatility, and movement-size calculations.

## Safe changes

- Add a 60% official-target floor while keeping every immutable locked winner visible for audit.
- Require independent book agreement plus steam or run-line confirmation for multi-reversal, divergent, compressed, resistance, or late-conflict rows.
- Exclude movement-against-selection and probability/direction-corrected rows from official-target status.
- Atomically claim one canonical pull per 15-minute sport/slate slot and fix the fallback to read the latest 500-row history rather than the first three rows.
- Keep playability and ML direction authority disabled until robust clean evidence exists.

These changes do not retroactively convert losses into wins and do not prove 90% accuracy.
