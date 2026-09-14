# Individual-starter model activation — September 14, 2026

This change pins the serving classifier to the candidate qualified by
[main evaluation 34816923903](https://github.com/KirtKurt/parlay-platform/actions/runs/34816923903).
The model version becomes `KS1-LGB-326657a4edfd-DP-9abc4f415689`.
Production publication and readback must be verified after this reference merges.

## Qualification evidence

The fixed training procedure used 4,413 games from March 18, 2025 through
August 22, 2026. Every training label preceded the earliest held-out prediction
cutoff. The test contains exactly 300 chronological games from August 22 through
September 13, 2026. This is **retrospective historical qualification**, not a
prospective trial. The test was not used to tune parameters or refit the model.

| Metric | Incumbent | Candidate |
| --- | ---: | ---: |
| Brier score | 0.24497061426416133 | 0.23875663965307406 |
| Log loss | 0.6827728234686443 | 0.6703034703168244 |
| Correct winner picks | 167/300 | 178/300 |

`accepted=true`, with 300/300 independently reconstructed and qualified context
rows. All eight consumed context fields are present in all 300 games. Each of
600 starter identities has pregame evidence: 584 archived timestamped feed
identities, eight retained pregame identities, and eight versioned T-10 identities.
Context bases are 596 current-season individual histories, two prior-year
individual histories, and two explicitly labelled league priors. The latter do
not represent observed individual results. See [cold-start rules](cold_start_context.md).

There are 669 observed individual-starter training rows per side and 4,268
historical context training rows per side. Earlier context training can use an
explicit, strictly prior rotation projection; it is not labelled a confirmed
starter. The 300-game test uses observed pregame identities on both sides.

The saved classifier has 459 input columns. Actual tree splits use 23 individual
starter fields and eight pitcher-context fields, including 30-day and last-three
ERA, last-three FIP, seven/30-day K%, K-BB%, WHIP, prior-year results, pitcher
quality, command, form and expected innings. The full used-feature list and
prespecified ablations are in `metrics.json`. These whole-model comparisons do
not establish that each individual field improves prediction.

## Immutable artifact

- Source commit: `b6ae991dcfac1c389260bb7fb9c08624fd724442` (PR #884).
- GitHub artifact: `10337182282`, ZIP SHA256
  `4af664aefb9f7a662f903cb6deb23d1520d7155e5eee01acd2e516a6d212ac80`.
- S3 model key:
  `mlb/experiments/ks1-phase2/5739768a8e163dc957c0c645c87bd0667a94e2a22bc383517536b31611e1182c/model.txt`.
- S3 version: `hNlAhvTqVgkO0qM1i0W3X0O857Bsp8pe`.
- Model SHA256: `326657a4edfdf5d77ea3e0582f3464a6d34ddae8e01bfab9d298416de39e0203`.
- All eight saved artifact files passed S3 readback. Downloaded file hashes,
  all 300 model predictions and metric values, and 600 context proof bindings
  and prior-game timestamps were independently checked before publication.
- The evaluation incumbent SHA matches the serving reference being replaced:
  `9de79b59e6d846da6210000bf13cde1090cafe871c4a477b075c06a89eb75390`.

## Serving and audit behavior

The Poisson models retain their existing hashes. The predecessor raw-model
version remains in the reviewed grading lineage, preserving its locked picks
and grades. New-model Platt and temperature state begin at identity; the old
model's fitted calibration is not transferred. The T-10 cutoff and 2 AM Eastern
nightly audit behavior remain in effect.

MLB Stats API provides official identities and prior box scores. Baseball Savant
provides attributable pitch/contact data; The Odds API provides markets; BBD is
a fixture cross-check, not a claimed pitching-stat source. Archived feeds are
retrieved retrospectively and validated against the provider's pregame timestamp.
Later official scoring corrections remain a stated historical limitation.

This activation does not complete the entire requested advanced-feature program.
September 13 Statcast data remains unavailable at the verified source snapshot.
Exact xERA, SIERA, Stuff+, Location+, Pitching+, and active-spin values remain
explicitly unavailable without supported sources. The candidate consumes no new
individual batter or reliever-quality/availability fields.
