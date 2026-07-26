# MLB V7 + V8 one-time slate test

This test is limited to the July 26, 2026 MLB slate.

- V7: apply the latest immutable historical optimizer candidate policy to current canonical home/away signals.
- V8: apply the corrected shadow market normalizer to full-game and first-five market observations.
- V8 remains `SHADOW_ONLY` and does not receive production or training authority.
- The test writes no prediction, lock, champion, cutover, or wager state.
- External provider use is hard-capped at 150 estimated credits.
- The generated report must contain all 15 games and must complete before the earliest game reaches T-minus-45.
