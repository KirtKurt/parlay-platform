# KS1 accuracy add-ons: comparison only

The tested additions did **not** beat the compact-core control. No model was
promoted, no serving reference changed, and nothing was deployed. This PR is
stacked on Phase 5 (#701) and uses existing archive sources only.

## Paired results

Train: **2025-04-01–2025-10-31, 2,320 games**. Test:
**2026-03-25–2026-09-09, 2,166 games**. All three comparisons use exactly the
same test IDs and labels. The compact control and add-on models use identical
training IDs and fixed hyperparameters. The holdout has been reported before;
this is a retrospective research comparison, not fresh prospective evidence.

| Version | LightGBM accuracy | LightGBM Brier ↓ | Poisson accuracy | Poisson Brier ↓ | ≥60% agreement accuracy | Agreement games |
|---|---:|---:|---:|---:|---:|---:|
| Accepted models before | 54.85% | 0.250463 | 53.23% | 0.250961 | 54.79% | 219 |
| Compact 35-core control | 52.31% | 0.251872 | 52.45% | 0.250568 | 55.51% | 227 |
| Core + supported additions | 51.66% | 0.252690 | 52.95% | 0.250637 | 54.35% | 276 |

Agreement means both models choose the same side **and both assign that side
at least 0.60**. Away confidence is `1 - P(home)`. These qualifying sets differ,
so their headline accuracies are not a paired comparison. On the 170 games
qualifying both before and after, both versions were **53.53%** accurate.

Against the accepted versions, after-minus-before Brier was +0.002227 for
LightGBM (worse) and -0.000324 for Poisson. A fixed-seed, 1,000-replicate date
block bootstrap gives 95% intervals `[+0.000599, +0.003844]` and
`[-0.001955, +0.001373]`, respectively. The small Poisson improvement is
uncertain. Against the 35-core control, both Briers worsened: +0.000818 and
+0.000069. No holdout tuning, threshold search, calibration, or post-test refit
was performed. The requested 0.60 threshold was fixed before evaluation.

## Feature budget and sources

The accepted classifier had 59 inputs. This experiment keeps **35 core
features**, selected by baseball role before viewing its metrics: both sides'
shrunk OPS/ISO and starter-group K-BB% in 10/30/75-day windows; 30-day offense
PA and starter BF/appearance counts; 1/3/5-day bullpen pitch counts; rest and
history counts; market home probability. No raw ERA or W-L is introduced.

The optional contract adds 13 values, their 13 missingness flags, and two
starter-projection missingness flags: **28 additional feature columns**, not
80. Training-only variation checks admit **six** additions, for **41 classifier
inputs**: home/away expected IP, their missingness flags, and home/away
projection-missing flags. Unsupported or constant training features are omitted
from models while their missingness remains visible in the feature table.
Poisson retains its directional core candidates and consumes the admitted
additions with training-only median imputation, indicators and scaling.

| Requested addition | Implemented evidence and limitation | Training coverage | Labeled test coverage |
|---|---|---:|---:|
| Starter expected IP | Past completed starts; shrunk team prior; retained strict-prior rotation projections when verified | 2,318 per side | 2,165 per side |
| Expected pitch count | Actual prior starter pitch counts, with team fallback; no BF-to-pitch-count fabrication | 0 | 399 per side; omitted from training |
| Opener flag | Explicit pregame role required; short realized outings are not opener labels | 0 | 0; missing |
| Lineup platoon count | Nine confirmed batters vs the observed opposing starter hand; switch hitters handled | 0 | 0; available on one unlabeled archived game |
| 2–5 hole availability | Earlier original pregame order compared with a later confirmed order; count of expected 2–5 hitters absent | 0 | 0; available on one unlabeled archived game |
| Team defense OAA/DRS | Neither was present. Legacy `defenseRating` is runs allowed and is deliberately excluded | 0 | 0 |
| Handed park factor | Separate LHB/RHB HR-per-PA ratios from prior completed games, shrunk with 1,000 league PA | 0 | 377 per split; omitted from training |
| Outdoor weather | Archived forecast temperature only, with pregame receipt and observed outdoor/open-roof evidence | 0 | 0; available on one unlabeled archived game |
| F5 runs and win | Target/parser/model path implemented; actual inning scores were not retained | 0 labels | 0 labels; training blocked |

The 855 retained rotation-projection records cover 2025-04-01–2025-06-05;
833 overlap this training table. They are estimates from prior rotation/rest
history, not observations of the actual target starter. The retained summaries
lost sample counts, so each exported expected-IP estimate is treated as one
pseudo-observation and shrunk 75% toward the team workload prior. Team workload
uses five league-prior games; an identified starter's own past starts use three
team-prior starts. All priors are formed strictly before the target cutoff.
The test period mostly relies on team priors. This coverage difference limits
what the experiment establishes about starter-specific accuracy.

The handed park feature measures **HR environment**, not a handed run factor.
The pitch cache contains handedness and PA-ending home-run events but no
inning/run progression. Missingness never becomes neutral defense, an available
hitter, a non-opener, an indoor weather effect, or an invented F5 result.

## Time and game-type protection

- Prior games must finish before `as_of_timestamp` and occur on earlier Eastern
  dates. Target, future, same-day and late-completing games do not enter workload
  or handed park calculations. Exhibition games are excluded.
- Original snapshots require game/team/start identity, capture before cutoff,
  a matching feature hash and pre-capture source receipts. Later lineup or
  starter observations cannot be attached to an earlier row.
- Raw/realized game-time weather is never read by the feature builder. Only an
  archived forecast with a timely receipt and open/outdoor park evidence is
  eligible. Closed roofs are rejected. Generic archived weather run factors
  lack the required roof binding and are excluded.
- Doubleheaders are flagged from official metadata or multiple retained games
  for the same team/date: 96 flagged rows, 411 explicitly single-game rows and
  3,989 unknown rows. An incomplete slate cannot establish a negative flag.
  The paired primary cohort retains these audit flags rather than changing
  which test games count.
- All 4,496 rows have `bullpen_game_status=unknown_no_archived_scheduled_role`.
  Missing starters and short final outings are not scheduled bullpen evidence.
- Season 2020 is excluded before splitting; this archive has zero 2020 rows.

## F5 boundary

`first_five` requires five distinct complete inning records with home and away
run counts. Full-game finals, F5 market prices, and `5/9 * full_game_runs` are
not labels. With sufficient real labels, the added path fits two F5 Poisson
regressions and a three-class LightGBM model for home/tie/away. It exports home,
away and tie probabilities separately; modeled F5 ties are not split into
half-wins. Synthetic training checks validate the code path only and supply
none of the reported MLB metrics.

**No real F5 model or F5 accuracy comparison was produced**, because there are
zero eligible archived F5 labels in the inspected sources. Every full-game
comparison row records that F5 status. No new vendor or provider archive was
requested to fill the gap.

## Reproduction, artifacts and changed files

Inputs are the accepted Phase 2 bundle from run `34431359524` and the
hash-verified retained-source capture from run `34436464918`. The capture read
4,610 compact games, 409 full boxes, four original snapshots, 119,830 retained
Statcast pitches, official final records, the latest BBS-prior manifest, and
the latest strict-prior official-context manifest. It made no provider calls
and no AWS writes. Snapshot counts increased from three to four between the
initial inventory and the frozen final capture; only snapshots before each
accepted row cutoff can contribute.

The existing `.github/workflows/ks1-phase3.yml` runs the comparison from those
fixed GitHub artifacts. Its final comparison job has no AWS credentials, no
scheduler, and no deployment step. Native LightGBM files, portable Poisson JSON,
metadata, feature table, per-game predictions, reliability buckets, source
receipts and comparison JSON are saved as a GitHub Actions artifact.

```bash
python -m pip install -r ks1/poisson-requirements.txt 'pytest>=8,<9'
python -m pytest -q tests/ks1_accuracy tests/ks1_phase2 tests/ks1_phase3
OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=1 python -m ks1.accuracy \
  --phase2-dir /path/to/accepted-phase2 \
  --archive-dir /path/to/accuracy-inputs \
  --output /tmp/ks1-accuracy-comparison
```

Changed files: `ks1/accuracy_data.py` (read-only source inventory),
`ks1/accuracy_features.py` (compact optional features and time guards),
`ks1/accuracy.py` (paired training/evaluation and portable artifacts),
`tests/ks1_accuracy/test_accuracy.py`, the existing Phase 3 workflow, and this
document. Sixteen focused tests pass locally, including future/target exclusion,
forecast-only/outdoor weather, switch hitters, pregame 2–5 absences, F5 missing
labels/ties, both-side 0.60 agreement, artifact reload and feature-budget checks.

Historical corrections remain possible in reconstructed prior statistics.
The result supports keeping the accepted models unchanged. Stop after this
comparison; no merge, deployment, simulation or next-phase work is included.
