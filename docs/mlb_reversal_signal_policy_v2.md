# MLB reversal signal policy v2

## Decision

Reversal count or reversal size alone is not a production edge. The June 28–July 22 canonical T-minus-45 audit contained 824 graded meaningful consensus reversal events and they moved toward the eventual winner 49.76% of the time. At one-event-per-game resolution, the latest, first, and largest meaningful reversal rules were 47.23%, 52.40%, and 48.71% accurate, respectively.

The only research pocket that reached a 70% lower confidence bound was the **largest pre-lock consensus market flip toward the selected side with a 2.00–2.99 percentage-point directional-leg amplitude**. It was 9-for-9 across seven slate dates, with a 95% Wilson lower bound of 70.09%. This was found after inspecting the data, so it is not a prospective guarantee and remains shadow-only.

## Runtime changes

The temporal feature extractor now measures the full movement path rather than only net velocity and a reversal count. For each horizon it records:

- net and gross movement, path efficiency, interval volatility, and maximum interval movement;
- directional-leg count, amplitude, duration, velocity, and reversal spacing;
- latest/prior leg size and duration, recovery ratio, and time since the last reversal;
- market-flip count, largest market-flip size and direction, age at the cutoff, and toward/against-side amplitudes.

The reversal similarity layer fingerprints size, age, reversal burden, recovery ratio, persistence, path noise, late direction, and independent confirmation. It never adds positive score. It can only label a research candidate or add risk blockers.

## 70% precision admission

No official recommendation is admitted merely because the model probability says 70%. Model confidence is not realized accuracy. A signal family must instead provide frozen validation evidence satisfying all of these requirements:

- at least 50 prospective holdout games over at least 20 slate dates;
- at least three chronological folds with at least 10 games each;
- outcomes untouched during rule construction and the rule frozen before evaluation;
- no post-discovery threshold tuning;
- a 95% Wilson lower precision bound of at least 70%;
- at least 20 recent games with at least 70% observed precision;
- exact signal-family identity and no fold below 60% accuracy.

When evidence is missing or any threshold fails, the immutable locked winner remains visible for audit, but the system abstains from an official recommendation.

## Prospective candidate

Signal family: `MLB_REVERSAL_LARGEST_TOWARD_MARKET_FLIP_2_TO_3PP`

At T-minus-45, choose at most one event per game: the largest directional leg that crosses the selected side through 50%, with amplitude at least 2.00 and below 3.00 percentage points. Capture its time, duration, recovery ratio, breadth when available, subsequent path, and outcome. Do not change the band or add filters until the frozen prospective evaluation is complete.

The candidate is not production-approved on the historical 9-for-9 result.
