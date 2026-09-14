# KS1 pitcher context at a season debut

The 300-game historical evaluation in [run 34812931737](https://github.com/KirtKurt/parlay-platform/actions/runs/34812931737) passed the Brier and log-loss comparison but had only 296 complete pitcher-context rows. All 600 pregame starter identities were available. The four missing rows had no earlier current-season MLB appearance for the named pitcher.

Direct MLB Stats API game-log verification on September 14, 2026 found:

| Game | Pitcher ID | Prior 2026 appearances | 2025 appearances |
| --- | --- | ---: | ---: |
| 822693 | 690279 | 0 | 0 |
| 823176 | 805074 | 0 | 0 |
| 822931 | 663795 | 0 | 9 |
| 824063 | 669203 | 0 | 11 |

The model-context fallback applies to every season debut, independent of outcomes:

1. Use the named pitcher's earlier current-season starts, or appearances if there are no starts.
2. When there are no current-season appearances, use the named pitcher's previous-season starts or appearances.
3. When neither season contains an appearance, use all strictly earlier current-season MLB starts as a league prior; on opening day, use the previous-season league starts.

Quality and command retain the existing FIP and K-minus-BB formulas. Individual histories retain last-three and last-five summaries. A league prior uses the entire selected league population for command and expected innings, rather than the last few league games. Velocity remains unavailable without its own supported source.

The profile explicitly stores `context_basis`. Historical reconstruction v2 binds the selected statistical population, source version, source hash, effective game dates, pitching-count hash, and independently verified pregame identity. Current and previous-season coverage must both be proven for any fallback. Incomplete observed pitching lines do not trigger a substitute prior.

These are model priors, not observed current-season form. The original 7-day, 30-day, last-three-start and previous-year result fields retain their existing observed counts, missingness and shrinkage rules. In particular, a pitcher with no appearances does not receive an invented ERA or game result.

The exact 300-game chronological holdout, lower-Brier/no-worse-log-loss requirement, independent evidence validation, and actual model feature-use requirement are unchanged. Reports separately count each context basis. A fresh comparison must pass before a separate reviewed serving-reference change. Historical evidence remains retrospective. T-10 records and the 2 AM Eastern audit are preserved.
