# KS1 temperature installation and Platt comparison

PR only, based on accepted Phase 6 commit
`d0ce308f037b623087ba868b89161598555630b6`. The simulation PR #704 remains
separate. This diff adds no simulation, features, vendors, model refit, production
engine switch, lock-rule change, audit rewrite, second scheduler, merge, or deployment.

## Installed temperature module

`src/temperature_calibrator.py` is standalone and uses Python's standard library.

```bash
python src/temperature_calibrator.py
```

The self-test passed. It verifies exact identity at T=1, the 30-row minimum,
T>1 for overconfident synthetic data, 80% weight on previous T, both metric
gates and idempotent refitting. The full local calibration/KS1 suite has
**81 passing tests**.

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

### Connected nightly grading and temperature fit

The requested post-ledger entry point is:

```python
from src.temperature_calibrator import fit_from_ledger

# Run only after the existing official ledger write succeeds:
fit_from_ledger(locked_official_rows)
```

`ks1/nightly.py` now supplies the missing caller inside the **existing**
`mlb-research-ingestion.yml` workflow. Its hourly cron is at minute 17. Grading
is due at **01:00 America/New_York**, including daylight saving time, after
the research source ingest and before daily publication. The due publisher
requires a `--sources-not-before` timestamp from this run's ingestion step
and complete, freshly observed retained finals. See `refresh-recovery.md` for
the KS1-only dispatch watchdog proposal. GitHub may delay runs; the next hourly
tick catches up if the nightly checkpoint is incomplete. A completed date never
fits temperature twice, including the repeated 01:00 hour in autumn. A completed
night does not stop subsequent hourly checks for late-arriving official finals.

The runner reads only existing versioned KS1 predictions and the retained
`prior-games.json` final-score source. It admits the original prospective lock
using the unchanged T-10 rule, preserves `p_raw`, and grades the **stored official
`p_home`**. Existing grades and their original probabilities are retained.
Conflicting outcomes or changed probabilities stop the run; no other pipeline's
ledger, audit, or predictions are modified.

The nightly checkpoint files live under the existing bucket and KS1 date prefix:

- `mlb/ks1/predictions-v1/date=YYYY-MM-DD/graded_ledger.json`: cumulative verified
  official grades, raw and locked probabilities, scores, and source evidence.
- `mlb/ks1/predictions-v1/date=YYYY-MM-DD/calibration_state.json`: the verified
  ledger receipt, temperature and Platt parameters, decisions, and completed date.

The date is the Eastern nightly processing date. Writes are conditional and
restricted to KS1 checkpoint files. **Ledger write and byte readback
must succeed before `fit_from_ledger(...)` runs.** A failed ledger write or
readback cannot fit either mapping. A failure saving the final state leaves the
previous accepted parameters authoritative; retry resumes the committed ledger
and does not apply shrinkage twice. This replaces the former caller-supplied
`--fit-temperature-after-ledger` receipt flag with an actual committed write.

### Late-final catch-up after a completed night

Previously, the completed-date gate skipped reading finals until the next
night. September 11 retained two official grades while two additional September
10 locks remained ungraded. The hourly runner now checks refreshed retained
finals after the existing 01:00 Eastern gate, even after that day's calibration
checkpoint completes. It still requires this ingestion attempt's fresh,
complete source receipts and original unchanged pre-cutoff prediction versions.

New grades are appended in immutable revisions:

- `date=YYYY-MM-DD/catchup=000001/graded_ledger.json`
- `date=YYYY-MM-DD/catchup=000001/calibration_state.json`

These are under the existing `mlb/ks1/predictions-v1/` prefix. Subsequent revisions
increment the six-digit number. The legacy nightly files and all earlier
revisions remain byte-for-byte unchanged. No new grades means no AWS writes.
Readers select the newest completed revision, validate its exact date/revision
ledger pointer and checksum, and use its cumulative official grades.

A catch-up state carries both nightly parameter objects unchanged and does not
call either fitter. It reports `calibration_status=deferred_to_next_nightly`.
Crossing 30 eligible rows during catch-up does not fit official temperature;
the next nightly checkpoint consumes the cumulative ledger under the existing
sample, shrinkage and rejection rules. The separate hourly Platt comparison's
existing seven-new-pick diagnostic behavior is unchanged; Platt does not become
the official publication transform.

The ledger is written and read back before the revision state is committed.
If state commit fails, readers continue using the previous completed revision;
the next tick resumes the same immutable ledger. Any additional finals arriving
during that retry wait for the following revision. Existing grades, `p_home`,
`p_raw`, label observation times and source evidence are preserved. Changed
outcomes or official probabilities fail closed. No prediction objects, T-10
rules, engine authority, LightGBM weights, AWS infrastructure or other engines
are changed. Local tests are not evidence that the production backlog cleared;
verify `completed_catchup`, ledger readback and retained official rows in the
first post-merge main workflow artifact.

The module saves `data/models/temperature.json` under the runner's output
directory. Accepted parameters also persist in the date checkpoint. Subsequent
daily input captures load that checkpoint ahead of older per-slate model copies,
so an empty slate or a disposable runner cannot reset T. Platt's seven-new-pick
gate runs after the same ledger commit. Hourly publication applies the retained
temperature and never refits it. The previous Inqsi and R8 audit jobs are untouched.

Writes require the existing main-branch workflow. PR/local verification uses
local files only. No AWS mutation or deployment was performed for this PR.

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
python -m ks1.nightly --inputs /path/to/verified/locked/capture.json --output /tmp/ks1-nightly
python -m ks1.platt --inputs /path/to/verified/locked/capture.json --output /tmp/ks1-calibration
```

The existing PR verification job downloads the pinned retained capture and
writes the ledger, comparison and parameter artifacts locally. It has no AWS credentials or
provider calls. No new archive is downloaded. Existing main-only publication
guards and conditional date writes remain in force.
