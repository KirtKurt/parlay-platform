# KS1 temperature installation and Platt comparison

PR only, based on accepted Phase 6 commit
`d0ce308f037b623087ba868b89161598555630b6`. The simulation PR #704 remains
separate. This diff adds no simulation, features, vendors, model refit, production
engine switch, lock-rule change, audit rewrite, scheduler, merge, or deployment.

## Installed temperature module

`src/temperature_calibrator.py` is standalone and uses Python's standard library.

```bash
python src/temperature_calibrator.py
```

The self-test passed. It verifies exact identity at T=1, the 30-row minimum,
T>1 for overconfident synthetic data, 80% weight on previous T, both metric
gates and idempotent refitting. The full local calibration/KS1 suite has
**65 passing tests**.

`calibrate_p(p_raw)` reads `data/models/temperature.json`. The existing KS1
publisher chooses the raw LightGBM probability first, then applies temperature
once and stores **`p_raw`** and **`p_home=p_lock`**, plus calibration method/version.
The scorer itself stays raw. Poisson probabilities, run means and totals remain
unchanged. Frozen rows are skipped without adding values or changing existing
ones. The `preserve_frozen` function's AST is identical to the accepted base.
The existing Phase 5 status handling is untouched.

The publisher defaults to temperature. Platt remains a separate explicit
`--calibration platt` option for the resumed comparison task; the two transforms
are never composed. Rerunning with a new mapping always starts from `p_raw`.
No `OFFICIAL_ENGINE` or production model configuration is changed.

Temperature fitting accepts verified locked official records only. It orders
by lock time, requires **30 graded rows**, fits the latest **100**, and proposes
`T_candidate = 0.8*T_previous + 0.2*T_fitted`. T is positive and bounded to
0.05–20. It promotes only when **both Brier and logloss do not increase against
either previous T or raw probabilities**. Rejected candidates leave T, n and
fitted_at intact. A signature prevents repeated fitting of the same ledger.

### Nightly hook is ready; the KS1 writer is missing

The requested post-ledger entry point is:

```python
from src.temperature_calibrator import fit_from_ledger

# Run only after the existing official ledger write succeeds:
fit_from_ledger(locked_official_rows)
```

Rows require game_id, p_raw, home_win, locked_at and graded_at. They must already
have been admitted as official locks by the caller. The module does not create
or rewrite a ledger, lock, pick, or AWS resource. It atomically replaces only its
local parameter JSON; the existing GitHub publisher can retain the parameter
artifact in the same KS1 date prefix.

**There is no KS1 01:00 ledger-writing job on the accepted base.** The existing
`InqsiNightlyAutopsyAt1AM` invokes `hello_world/inqsi_autopsy_scheduler.py` at
`cron(13 6 * * ? *)` and grades the separate Inqsi pipeline. The canonical MLB
settlement job is another existing authority. Neither is rewritten or used as
a substitute KS1 ledger. The after-write connection therefore remains pending
identification of the KS1 writer. The hourly prediction job does **not** fit T.

The calibration CLI exposes `--fit-temperature-after-ledger` only when the
capture includes a completed `ledger_write_completed_at` receipt. This is an
internal callback contract, not a newly assumed provider field. Current
read-only captures intentionally do not claim such a ledger write.

## Resumed Platt work

`ks1/platt.py` fits exactly one column `logit(p_raw)` with sklearn
LogisticRegression. C is 0.3 below 100 graded games, then 1.0. It refits after
seven new distinct graded official picks, trains on the latest min(100, all),
and uses `0.8*old + 0.2*new` coefficients below 100 games. A higher recent-50
Brier than raw rejects the candidate and keeps previous A, B, n and fitted_at.
Parameters are stored in `data/models/platt.json`; there is no isotonic model
and no LightGBM refit.

The requested recent-data rejection gates overlap their fitting windows. They
are explicitly labeled diagnostics, not unseen-test performance. The separate
raw/temperature/Platt comparison is chronological: each test probability uses
only labels observed before that test snapshot. All methods report on the same
latest min(50, all) locked games. Unknown historical grade availability is not
backdated to the final inning time. First observed label times are retained in
calibration metadata without modifying the source audit.

`ks1/platt_inputs.py` reads existing versioned KS1 predictions and retained final
scores. Original pre-cutoff S3 storage, unchanged frozen values, stable team IDs
and the correct official raw model are required. R8, reconstructed historical
probabilities and official simulation picks cannot enter the LGB calibration set.

## Observed result and five-row sample

The verified capture from CI run **34442723793**, at **2026-09-10 05:51:41 UTC**,
contains **zero KS1 prediction objects and zero locked graded rows**. No genuine
calibrator can be fitted or evaluated from that capture. T stays **1.0**, n=0,
fitted_at=null; Platt stays A=1, B=0, n=0, fitted_at=null. These are explicitly
unfitted identity defaults, not invented learned parameters.

| Method | n | Brier | Logloss |
| --- | ---: | ---: | ---: |
| Raw | 0 | N/A | N/A |
| Temperature | 0 | N/A | N/A |
| Platt | 0 | N/A | N/A |

The five raw probabilities below come from the retained 2026-09-10 slate capture
at 03:20:07 UTC. They demonstrate the current publish transform, not calibration
accuracy. Source SHA and full-precision values are in `calibration_sample.json`.

| Game ID | p_raw | p_lock, T=1 |
| --- | ---: | ---: |
| 824872 | 0.462811 | 0.462811 |
| 823413 | 0.671060 | 0.671060 |
| 823088 | 0.494803 | 0.494803 |
| 823499 | 0.740772 | 0.740772 |
| 824550 | 0.535060 | 0.535060 |

```bash
python -m pip install -r ks1/poisson-requirements.txt pytest PyYAML
python src/temperature_calibrator.py
python -m pytest -q tests/ks1 tests/ks1_phase4 tests/ks1_phase5 tests/ks1_calibration
python -m ks1.platt --inputs /path/to/verified/locked/capture.json --output /tmp/ks1-calibration
```

The existing PR verification job downloads the pinned retained capture and
writes comparison/parameter artifacts locally. It has no AWS credentials or
provider calls. No new archive is downloaded. Existing main-only publication
guards and conditional date writes remain in force.
