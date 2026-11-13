# MLB Moneyline Reversal-Size Audit — 2026-07-23

## Scope and definitions

- Source: canonical 15-minute MLB DynamoDB pull history.
- Study dates: 2026-06-28 through 2026-07-22.
- Pregame boundary: only observations at or before T-minus-45.
- Measurable cohort: 298 games had at least two pre-T45 moneyline observations; 292 mapped to a final winner.
- A reversal leg begins when the sign of the de-vigged consensus home-moneyline probability change switches.
- Tiny step changes of 0.05 percentage point or less are ignored.
- A meaningful reversal leg is at least 0.30 percentage point.
- The audit found 2,253 consensus reversal legs, including 838 meaningful legs across 275 games.
- It also found 9,901 individual-book reversal legs, including 7,872 meaningful legs.

## Repeated reversal sizes across games

Sizes below are consensus reversal-leg amplitudes rounded to the nearest 0.25 percentage point.

| Rounded size | Events | Games | Slates | Reversal pointed toward winner |
|---:|---:|---:|---:|---:|
| 0.50 pp | 281 | 164 | 22 | 48.39% |
| 0.25 pp | 149 | 108 | 21 | 51.03% |
| 0.75 pp | 119 | 97 | 22 | 45.22% |
| 1.00 pp | 83 | 75 | 19 | 54.88% |
| 1.25 pp | 40 | 39 | 18 | 55.00% |
| 1.50 pp | 38 | 35 | 17 | 45.95% |
| 1.75 pp | 20 | 19 | 15 | 30.00% |
| 2.00 pp | 26 | 25 | 13 | 52.00% |
| 2.25 pp | 21 | 20 | 12 | 66.67% |

The common 0.25/0.50/0.75/1.00-point clusters occur across many games and dates. Size alone is not a winning signal: the high-volume clusters are near 50%, and the apparently strong 2.25-point result has only 21 events and was found retrospectively.

## Repeated size inside the same game

- 126 of 275 games with a meaningful consensus reversal repeated the same 0.25-point size bucket at least twice.
- 39 games repeated the same bucket at least three times.
- In the 124 repeat-size games with a final outcome, the final meaningful reversal pointed toward the winner 54 times (43.55%) and away from the winner 70 times (56.45%).
- In non-repeat games, the final meaningful reversal was essentially neutral: 74 of 147 (50.34%) pointed toward the winner.

Repeated same-size reversals therefore resemble a whipsaw/noise state more than a directional confirmation.

## Similarity across all books

- Six of 838 meaningful consensus reversal events had a same-direction reversal within 30 minutes at all eligible books (normally 11).
- None of those six had all-book amplitudes within 0.50 percentage point of one another.
- Only two of the six all-book same-direction events pointed toward the eventual winner.
- Twenty-three of 838 events had at least four books whose reversal amplitudes were within 0.50 percentage point.
- Four of 838 events had at least four books within 0.25 percentage point; two pointed toward the winner.

Exact or near-exact all-book size matching is rare and did not improve outcome direction in this cohort.

## Book dependence and price-ladder signatures

- BetOnlineAG and LowVig were empirically near-duplicates: across 262 synchronized reversal events, amplitudes were exactly equal 94.27% of the time, within 0.25 point 96.56%, with amplitude correlation 0.992.
- BetUS was within 0.25 point of BetOnlineAG/LowVig about 61% of synchronized events.
- Fanatics and WilliamHill US were exactly equal 36.92% and within 0.25 point 50.77% across 65 synchronized events.
- Fanatics generated a reversal in the rounded 1.00-point bucket on 579 of 920 meaningful book reversals (62.93%).
- WilliamHill US used the 1.00-point bucket on 75 of 234 reversals (32.05%).
- Bovada, MyBookieAG, and BetUS most commonly landed in the 0.50-point bucket: 31.50%, 34.14%, and 30.64%, respectively.

These repeated sizes are partly book-specific price-grid signatures. Raw book count overstates independent confirmation when correlated books are counted separately.

## Most useful retrospective size regimes

Use only the final meaningful reversal in each game:

| Pattern | Graded games | Recommended interpretation | Historical success |
|---|---:|---|---:|
| Final reversal under 1.00 pp | 173 | Fade / do not follow | 99/173 = 57.23% |
| Under 1.00 pp at 3+ independent book families | 110 | Fade candidate | 69/110 = 62.73% |
| Under 1.00 pp at 50%+ independent families | 50 | Fade candidate | 32/50 = 64.00% |
| Final reversal 1.00–1.49 pp | 33 | Neutral | 16/33 = 48.48% followed |
| Final reversal 1.50–2.99 pp | 51 | Follow candidate | 30/51 = 58.82% |
| 1.50–2.99 pp at 3+ independent families | 32 | Follow candidate | 20/32 = 62.50% |
| Market-crossing reversal | 12 | Follow candidate, insufficient sample | 7/12 = 58.33% |

The strongest sample-supported discovery is the broad micro-reversal fade: under 1.00 point at three or more independent book families. Its 95% Wilson interval is approximately 53.4%–71.2%, and its two-sided 50/50 binomial p-value is approximately 0.0097. This was discovered retrospectively and therefore requires prospective shadow validation before production use.

The 1.50–2.99-point follow rule is promising but not yet proven; its 95% interval is approximately 45.3%–77.1%.

## Literal Monday slice

- 36 Monday games were measurable.
- 35 had a meaningful final reversal and a final outcome.
- The final reversal pointed toward the winner 14 times (40.00%) and away from the winner 21 times (60.00%).

This does not establish a Monday effect; the sample is small and the confidence interval is wide. If “Monday” meant “moneyline,” the main tables above are the moneyline analysis.

## Recommended signal treatment

1. Do not score raw reversal count or exact size as a universal positive.
2. Deduplicate BetOnlineAG and LowVig into one empirical book family.
3. Treat repeated same-size reversal buckets within one game as whipsaw risk.
4. Treat final broad reversals under 1.00 point as a prospective mean-reversion/fade feature, not an automatic pick flip.
5. Treat final 1.50–2.99-point reversals across at least three independent families as a prospective continuation feature.
6. Require persistence, book-family breadth, and fundamentals alignment before any reversal changes the official side.
7. Run both rules shadow-only and evaluate untouched future games before changing production weights.
