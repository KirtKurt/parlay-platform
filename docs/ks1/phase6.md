# KS1 Phase 6: bullpen and park features only

This experiment preserves the accepted Phase 2/3 champion, adds supported
bullpen features, and compares both models on the same chronological holdout.
It uses existing retained data only. No Monte Carlo, PA simulation, provider
calls, AWS writes, serving-reference changes or deployment is included.

## Features and source limits

For each team the builder supplies four values and their missingness flags:

- **Bullpen rest days:** complete calendar days since the last observed relief
  appearance. An outing with pitches but zero outs still counts as usage.
- **30-day relief BB/9:** exact opposing-team walks minus that team's starter
  walks, divided by relief outs and multiplied by 27. Compact archives retain
  those counts. Full-box relief totals independently check the decomposition.
  The rate shrinks toward prior current-season league relief with 90 outs
  (30 innings) of prior weight. This measures walk prevention, not every
  dimension of pitching quality.
- **30-day relief K–BB%:** sums only non-starter pitching appearances from
  actual game boxes and shrinks with 100 batters faced toward the prior league
  relief rate. Compact summaries lack relief strikeouts/BF; these values remain
  missing rather than using starter metrics or raw ERA as a substitute.
- **Relievers used over two days:** distinct relief pitchers in complete prior
  full boxes. This is a recent-use proxy for availability. Current roster,
  medical availability and manager decisions are unverified and flagged.

Only per-game `stats.pitching` is read. Postgame `seasonStats` is ignored.
An incomplete observed role classification cannot silently become zero relief
usage. Negative or inconsistent walk decompositions become missing and are
counted in the source proof. Full boxes take precedence over compact versions
for the same game; cross-version walk differences are reported.

Prior games must finish before `as_of_timestamp`, occur on earlier Eastern
dates, and belong to the current season. Target, future and same-day games are
excluded from feature history. Known missing boxes in the original 75-day
audit make the affected team's new bullpen features unavailable. Cold starts
do not borrow future league priors. No target rows are dropped.

Two park values and their missingness flags complete a **20-column numeric
candidate contract**; only eligible columns enter the trained models:

- A static prior park run factor comes from verified historical-context
  snapshots or original pregame venue summaries. The former retained a ratio
  but lost its exact sample count; its source requires at least 10 venue games.
  That lower bound supplies conservative support against a 20-game neutral
  prior. Original venue summaries retain their actual sample count.
- The effective park factor equals the static factor unless an original,
  timely archived forecast and outdoor/open-roof evidence are present. Then
  it uses the **existing repo's temperature-only heuristic**:
  `static_factor * clip(1 + 0.002 * (forecast_F - 70), 0.85, 1.15)`.
  Source: `weather_run_factor` in
  `hello_world/mlb_v8_historical_point_in_time_context_v1.py`.
  This is not a newly calibrated environmental model. Missing wind/humidity
  are not fabricated. Closed roofs, late forecasts and realized game-time
  weather never provide an adjustment. Missing static factors stay missing.

Every row records bullpen and environment status. Forecast availability on an
unlabeled game does not establish weather coverage in the training/test cohort.

## Fixed comparison

The restored champion is the hash-verified Phase 2 LightGBM artifact from run
`34431359524` (59 ordered inputs) plus the Phase 3 Poisson artifact from run
`34432200300` (31 ordered inputs per run regression). Exact hashes and full
lists are exported in the artifact. No old rejected add-on groups are restored.

Train: **2025-04-01–2025-10-31, 2,320 games**. Test:
**2026-03-25–2026-09-09, 2,166 games**. Ordered game IDs, dates, labels and
original feature values are asserted unchanged. Model parameters remain fixed.
The 40% missingness rule is applied to each new raw value in both splits before
training; indicators do not turn missing measurements into observed values.
The Poisson home-run model receives the opposing bullpen's additions, and
vice versa. Eligible environment inputs would apply to both sides.

This is an already-reported retrospective holdout, not prospective validation.
No threshold search, outcome-driven feature iteration or hyperparameter tuning
is performed after the comparison.

## Results

Both Briers improved slightly. These are measured differences on the reused holdout, not a claim of statistical significance or fresh prospective accuracy.

| Model | Before Brier | After Brier | Delta | Before accuracy | After accuracy |
|---|---:|---:|---:|---:|---:|
| lightgbm | 0.250463 | 0.250174 | -0.000288 | 54.85% | 55.31% |
| poisson | 0.250961 | 0.250929 | -0.000032 | 53.23% | 53.23% |

Agreement accuracy when both models choose the same side at ≥60% changed from **54.79% on 219 games** to **54.11% on 231 games**. These qualifying sets differ; lower Brier does not imply improvement in this filtered accuracy metric. Logloss changed from 0.694424 to 0.693905 for LightGBM and from 0.695379 to 0.695335 for Poisson.

The model admits **eight additional columns**: both sides' rest days and 30-day BB/9 plus each value's missingness flag. LightGBM therefore has 67 inputs, preserving all 59 champion inputs. Each run regression receives the opposing side's four added columns, for 35 inputs.

| New value | Train missing home / away | Test missing home / away | Model use |
|---|---:|---:|---|
| Bullpen rest days | 1.64% / 1.81% | 0.69% / 0.69% | Admitted |
| Relief BB/9, 30d | 1.64% / 1.81% | 0.69% / 0.69% | Admitted |
| Relief K–BB%, 30d | 100.00% / 100.00% | 99.77% / 99.77% | Audit only |
| Relievers used, 2d | 100.00% / 100.00% | 82.27% / 82.27% | Audit only |

Static park factors are available on **813 training rows**, but zero labeled test rows. An outdoor temperature adjustment is available for **one unlabeled game**; neither labeled split has a usable weather observation. Park/environment values remain in the audit with status fields and are excluded from the models by the coverage gate. Consequently, **the Brier comparison tests bullpen rest/walk prevention, not weather effects or reliever K–BB%**.

The source checks verified all **818 full-box team walk identities**. All **808 overlapping compact/full-box relief-walk comparisons matched**, with zero invalid decompositions. The builder retained 9,230 team-game history rows and 2,704 relief appearances, always filtering them to the target cutoff.

Train-ID SHA-256: `0219fa6b09429a503f99cbd858f0875d2f09dd930a6aded4dad9a788c218f9da`. Test-ID SHA-256: `fe830d81988a78f552d10aa5e7b3c2e458c6d34217fbbdbf8120a26dc62c0f8a`. No rows were dropped. No champion is replaced or proposed for automatic promotion; stop after this feature comparison.

## Reproduction and artifacts

The input capture is the frozen artifact from GitHub run `34436464918`; its
hashes and all accepted Phase 2 file hashes are verified. The Phase 6 job runs
inside the existing Phase 3 comparison workflow and downloads those GitHub
artifacts. It has no AWS credentials, scheduler or deployment step.

```bash
python -m pip install -r ks1/poisson-requirements.txt 'pytest>=8,<9'
python -m pytest -q tests/ks1_phase6 tests/ks1_accuracy tests/ks1_phase2 tests/ks1_phase3
OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=1 python -m ks1.phase6 \
  --phase2-dir /path/to/accepted-phase2 \
  --archive-dir /path/to/accuracy-inputs \
  --output /tmp/ks1-phase6
```

Use an empty output directory. Outputs include restored champion weights,
native LightGBM and portable Poisson candidate artifacts, exact feature lists,
per-game before/after predictions, coverage/status audit, cohort/source
receipts, and comparison CSV/JSON. The model input table contains only the
original champion features and admitted additions. The separate Phase 6 audit
table retains unavailable feature values and flags for review.

Changed files: `ks1/phase6_features.py`, `ks1/phase6.py`,
`tests/ks1_phase6/test_phase6.py`, `.github/workflows/ks1-phase3.yml`, and this
document. Thirty focused tests pass locally, covering temporal exclusion,
missing-history guards, walk decomposition, relief-only stats, season-stat
exclusion, forecast/roof gating, static fallback, source hashes, feature
missingness, unchanged cohorts and native artifact reloads.
