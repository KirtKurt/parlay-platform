# KS1 Phase 3: dual-Poisson comparison

This experiment uses the accepted Phase 2 artifact from GitHub run 34431359524.
Its artifact receipt is pinned by SHA-256; every input file must match its
recorded hash before training. There are no AWS writes, provider calls, archive
downloads, serving changes, merges, or deployments. Phase 2 is still an open
draft, so this PR is stacked on its branch.

Two log-link Poisson regressions predict final home and away runs. Both use fixed
L2 alpha=1, 2,000 maximum iterations and tolerance 1e-8; convergence failure aborts.
Each uses its own team offense (OPS, ISO, PA, games in 10/30/75-day windows), the
opposing starter/team-starter process and workload columns, opposing bullpen
workload (1/3/5 days), and both teams' rest/history. Features unavailable or
constant in training are excluded. Existing small-sample feature shrinkage is
preserved. Median imputation, missing indicators and standardization are fitted
on training data only. There is no holdout parameter selection or calibration.

Park, weather and individual-starter fields are absent from the training period;
they are explicitly omitted. This uses team offense, not confirmed lineups.
Market probability is excluded from Poisson; the accepted LightGBM includes it.

The training split is exactly 2025-04-01 to 2025-10-31 (2,320 games). The test
split is exactly 2026-03-25 to 2026-09-09 (2,166 labeled games). The saved Phase 2
LightGBM and better-record predictions are joined by game ID and their dates,
teams and labels verified. Their accuracy and Brier must reproduce Phase 2
within 1e-12. Neither comparison method is retrained or modified.

For independent H~Poisson(lambda_home), A~Poisson(lambda_away):

- Home team total: lambda_home.
- Away team total: lambda_away.
- Expected game total: lambda_home + lambda_away.
- P(home win): P(H>A) + 0.5 P(H=A), evaluated using the Skellam distribution.

The 50/50 tie allocation is an explicit approximation to resolve modeled ties;
it is not an extra-inning simulator. Rates target final full-game runs, including
extra innings. Independence, Poisson dispersion and the tie rule are baseline
assumptions. No PA or inning simulation is implemented.

Outputs are comparison.json (accuracy, Brier, all reliability buckets, run MAE,
RMSE and deviance), predictions.parquet/CSV (every test game's probabilities and
team/game totals), poisson_model.json (both models and preprocessing, no pickle),
input_receipt.json, and reliability.csv. Reloaded JSON rates must match the
fitted estimators within 1e-12. Tests independently enumerate Poisson scores,
check preprocessing, require matching test games and reject unaccepted inputs.

## Run locally

Use Python 3.11+ and extract the accepted Phase 2 bundle to a separate directory.

```bash
python -m pip install -r ks1/poisson-requirements.txt 'pytest>=8,<9'
python -m pytest -q tests/ks1_phase2 tests/ks1_phase3
python -m ks1.poisson --phase2-dir /path/to/phase2 --output /tmp/ks1-phase3
```

The PR-only workflow downloads the retained GitHub artifact, verifies its hashes,
runs the comparison and uploads outputs. It has only contents/actions read
permissions and no AWS credentials or scheduler. After GitHub retention expires,
use the saved Phase 2 bundle with the local command; never substitute new data.

Implementation references: [scikit-learn PoissonRegressor](https://scikit-learn.org/1.6/modules/generated/sklearn.linear_model.PoissonRegressor.html)
and [SciPy Skellam distribution](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.skellam.html).

Stop after the comparison; Phase 4 is not authorized by this change.
