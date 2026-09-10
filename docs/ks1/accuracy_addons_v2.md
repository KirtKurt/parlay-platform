# KS1 addon-v2: champion plus isolated keepers

**Only G is kept. Addon-v2 has zero new predictor columns and is numerically
equivalent to the champion.** The available A additions worsened both models;
all non-G groups exceeded the 40% missingness cutoff. F5 remains blocked by
zero real archived inning-score labels.

This follow-up restores the exact accepted Phase 2/3 pair as the research
baseline. The first-pass reduction to 35 classifier inputs was a confound and
is not used here. Serving references remain unchanged. No vendor, provider
archive download, AWS write, merge or deployment is part of this experiment.

## Champion and frozen cohort

The Phase 2 LightGBM artifact is from GitHub run `34431359524`, SHA-256
`9de79b59e6d846da6210000bf13cde1090cafe871c4a477b075c06a89eb75390`.
Its **59 ordered features** are loaded from its verified `feature_list.json`,
checked against the native model, and preserved without reselection. The list
contains the same 29 fields for each team plus `market_home_prob`:

- Bullpen outs and pitches for 1/3/5 days (6).
- Team history games and rest days (2).
- Offense games, ISO, OPS and PA for 10/30/75 days (12).
- Starter-group appearances, BF and K-BB% for 10/30/75 days (9).

The accepted Phase 3 dual-Poisson artifact is from run `34432200300`, SHA-256
`9abc4f4156890c70c606f9a4c6a4198ac9fb7446e5def98351b043b7e37b2a75`.
Each run regression retains its **31 ordered features**: its own team's 12
offense fields; opposing bullpen outs/pitches (6), starter-group
appearances/BF/K-BB% (9); and both teams' history/rest (4). The complete exact
lists are exported with the restored champion and every diagnostic model.

Train: **2025-04-01–2025-10-31, 2,320 games**. Test:
**2026-03-25–2026-09-09, 2,166 games**. The input receipt, model hashes,
ordered game IDs, dates, full-game labels and original feature values are
verified. All full-game toggles retain the same rows. H drops zero rows;
2020 is a flag, with zero 2020 games in this frozen table.

Hyperparameters are the accepted Phase 2/3 settings. The experiment does not
search thresholds, tune hyperparameters, or perform a later full-data refit.
Native export/reload checks use `1e-12`. Refits against an accepted model allow
`1e-8` numerical error from optimization/array layout; the local no-feature
Poisson refit differed by about `1e-10` in probabilities, without changing
picks. LightGBM reproduced the champion probabilities exactly.

## Isolation and decision rules

Each of A–H starts from the champion, with no other group's additions. Only
columns varying in training enter a fitted model. Missingness indicators are
allowed for that group's inputs; unavailable values are never fabricated.

The declared keep rule uses **LightGBM home-win Brier** as the primary metric;
Poisson metrics are reported alongside it. A non-G full-game group requires
Brier improvement beyond `1e-12`. Worse Brier and worse logloss means drop.
Before that metric gate, **more than 40% missing in either split means drop**.
Missingness is the mean fraction of missing raw-value cells across all named
members of that group; missingness/projection indicator columns do not count
as observations. Per-column and any-member-missing rates are also exported.
Thus A cannot hide missing opener fields behind available expected IP.

**G is retained by instruction as a forecast/as-of safeguard**, without a
requirement for improvement or forecast coverage. The champion has no weather
inputs, so G does not remove demonstrated leakage from these fitted models
and does not add a predictor. Weather values must come from verified original
pre-cutoff forecasts with outdoor/open-roof evidence; realized temperature and
unverified wind are not passed through.

F only trains a separate first-five-inning head when actual inning labels
exist. F5 labels never enter full-game X or y. A separate F5 head has no effect
on full-game predictions, so its full-game comparison row is the champion;
the report separately records eligible F5 label counts and blocked status.

The requested keeper decisions use the already-reported test set. The v2
comparison therefore **is not fresh prospective validation**. No assertion of
future improvement follows from selecting groups on this holdout.

## Results

All full-game rows below use the same 2,166 test IDs. Agreement means both models select the same side and each assigns it at least 0.60; away confidence is `1 - P(home)`. The qualifying sets differ. Missingness is shown as training/test percentages.

### LightGBM

| Group | n_test | Brier | Logloss | Accuracy | Agreement accuracy (n) | Missing train/test | Decision |
|---|---:|---:|---:|---:|---:|---:|---|
| Champion | 2,166 | 0.250463 | 0.694424 | 54.85% | 54.79% (219) | — | Reference |
| A | 2,166 | 0.250491 | 0.694485 | 53.92% | 53.05% (279) | 50.04% / 50.02% | Drop |
| B | 2,166 | 0.250463 | 0.694424 | 54.85% | 54.79% (219) | 100.00% / 100.00% | Drop |
| C | 2,166 | 0.250463 | 0.694424 | 54.85% | 54.79% (219) | 100.00% / 100.00% | Drop |
| D | 2,166 | 0.250463 | 0.694424 | 54.85% | 54.79% (219) | 100.00% / 100.00% | Drop |
| E | 2,166 | 0.250463 | 0.694424 | 54.85% | 54.79% (219) | 100.00% / 88.40% | Drop |
| F | 2,166 | 0.250463 | 0.694424 | 54.85% | 54.79% (219) | 100.00% / 100.00% | Drop |
| G | 2,166 | 0.250463 | 0.694424 | 54.85% | 54.79% (219) | 100.00% / 100.00% | Keep safeguard |
| H | 2,166 | 0.250463 | 0.694424 | 54.85% | 54.87% (226) | 65.89% / 59.85% | Drop |
| addon-v2 | 2,166 | 0.250463 | 0.694424 | 54.85% | 54.79% (219) | — | G only |

### Dual-Poisson

| Group | n_test | Brier | Logloss | Accuracy | Agreement accuracy (n) | Missing train/test | Decision |
|---|---:|---:|---:|---:|---:|---:|---|
| Champion | 2,166 | 0.250961 | 0.695379 | 53.23% | 54.79% (219) | — | Reference |
| A | 2,166 | 0.251179 | 0.696198 | 54.16% | 53.05% (279) | 50.04% / 50.02% | Drop |
| B | 2,166 | 0.250961 | 0.695379 | 53.23% | 54.79% (219) | 100.00% / 100.00% | Drop |
| C | 2,166 | 0.250961 | 0.695379 | 53.23% | 54.79% (219) | 100.00% / 100.00% | Drop |
| D | 2,166 | 0.250961 | 0.695379 | 53.23% | 54.79% (219) | 100.00% / 100.00% | Drop |
| E | 2,166 | 0.250961 | 0.695379 | 53.23% | 54.79% (219) | 100.00% / 88.40% | Drop |
| F | 2,166 | 0.250961 | 0.695379 | 53.23% | 54.79% (219) | 100.00% / 100.00% | Drop |
| G | 2,166 | 0.250961 | 0.695379 | 53.23% | 54.79% (219) | 100.00% / 100.00% | Keep safeguard |
| H | 2,166 | 0.251324 | 0.696114 | 53.05% | 54.87% (226) | 65.89% / 59.85% | Drop |
| addon-v2 | 2,166 | 0.250961 | 0.695379 | 53.23% | 54.79% (219) | — | G only |

A admits only six expected-IP/provenance columns; opener inputs are entirely missing. B, C, D and E admit no training features, so their unchanged metrics are not evidence those baseball signals lack value. H admits only a doubleheader-missingness indicator; LightGBM is unchanged and Poisson worsens. F adds no full-game input and has **zero training and zero test F5 labels**; no real F5 head was trained. G has no predictor to add because the champion contains no weather inputs.

The final keeper set is **G only**. LightGBM v2 is exactly equal to the champion. Poisson Brier differs by about `-1.75e-12`, numerical refit noise; both models have identical winner picks and the same 219-game agreement cohort. This is no accuracy improvement. The final feature table has 4,496 rows, the 59 champion feature columns, metadata/labels, and **zero additional predictor columns**.

Frozen train-ID SHA-256: `0219fa6b09429a503f99cbd858f0875d2f09dd930a6aded4dad9a788c218f9da`. Test-ID SHA-256: `fe830d81988a78f552d10aa5e7b3c2e458c6d34217fbbdbf8120a26dc62c0f8a`. `cohort.json` includes the ordered IDs and an empty dropped-ID list.

## Reproduction and artifacts

Inputs are the accepted Phase 2 artifact and the frozen retained-archive
capture from run `34436464918`. The current job in the existing Phase 3
workflow downloads those GitHub artifacts; it has no AWS credentials or
deployment step. No second scheduler is introduced. Use an empty output
directory so an old experiment cannot leave stale model files in the result.

```bash
python -m pip install -r ks1/poisson-requirements.txt 'pytest>=8,<9'
python -m pytest -q tests/ks1_accuracy tests/ks1_phase2 tests/ks1_phase3
OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=1 python -m ks1.accuracy_v2 \
  --phase2-dir /path/to/accepted-phase2 \
  --archive-dir /path/to/accuracy-inputs \
  --output /tmp/ks1-addon-v2
```

Outputs include the exact restored champion, isolated diagnostic models,
`comparison.csv` / `comparison.json`, long-form per-game predictions, a cohort
ID receipt, source receipts, and the rebuilt `addon-v2` model pair and policy.
The **v2 feature table contains only original champion columns plus keepers**;
rejected experimental columns are not appended to its contract.

Changed files for this follow-up: `ks1/accuracy_v2.py`,
`tests/ks1_accuracy/test_accuracy_v2.py`, `.github/workflows/ks1-phase3.yml`,
the first-pass documentation notice, and this document. Twenty-four focused
tests verify isolation, unchanged labels/cohorts, group missingness, the keep
rules, forecast safeguards, F5 target separation and native artifact reloads.
