# KS1 prospective publication and grading release

This release lands the runtime from the accepted Phase 2/3 models, Phase 4 daily
publisher, Phase 5 refresh, and temperature/Platt PR #705 onto current main.
It includes the runtime dependencies and their tests from PRs #698–701 and
#705. The Phase 6 comparison and accuracy add-on research branches are not
runtime dependencies; their experimental models and training workflows are
not activated. Simulation PR #704 is separate.

The serving references remain exactly those verified in the daily-prediction
PR: the frozen LightGBM artifact and portable dual-Poisson artifact in
`ks1/model_refs.json`. There is no LightGBM refit or engine switch.

## Release path

The existing `.github/workflows/mlb-research-ingestion.yml` is the deploy path
for this job. A main-branch push affecting its KS1 paths runs the published
code using the existing AWS credentials and discovered artifact bucket. The
same workflow runs hourly afterward. No SAM/Lambda infrastructure or other
sports' production authority changes are needed.

1. Verify the release PR with the complete runtime test suite and retained
   calibration capture. The calibration PR job has no AWS credentials.
2. Merge the checked release head to main. The existing main workflow runs
   source ingestion, then nightly grading if due, live input capture,
   calibration parameter loading and daily prediction publication.
3. Verify the run's `ks1-nightly-*` and `ks1-daily-*` artifacts. Publication must
   report `published=true` with successful S3 readback, the expected KS1 date
   prefix and the correct serving references. Each new eligible game's
   original probability must be saved before its T-10 cutoff.
4. At the next 03:00 Eastern run, completed games with original KS1 lock evidence
   become graded rows. Pending games wait for verified final results. The ledger
   is committed and read back before either mapping is fitted.

The hourly cron is at minute 17. DST and the 03:00 Eastern delayed-run catch-up
are handled inside `ks1/nightly.py`. See `refresh-recovery.md` for the proposed
KS1-only dispatch watchdog, source-freshness gate, and activation boundary.
The unchanged Phase 1 main workflow may also rebuild its game table from
existing retained data; it does not train or replace the serving models.

## Evidence boundaries

Local/PR success verifies code and retained-data behavior, not deployed
execution. Production evidence must come from the post-merge main run. An
empty graded ledger is valid before prospective predictions finish; it must
never be filled with retrospective reconstructed predictions or other engines'
picks. Temperature stays T=1 until 30 eligible graded official KS1 rows exist,
then uses the rolling last 100 and both metric gates. Existing locked
probabilities are never rewritten.

```bash
python src/temperature_calibrator.py
python -m pytest -q tests/ks1 tests/ks1_phase2 tests/ks1_phase3 tests/ks1_phase4 tests/ks1_phase5 tests/ks1_calibration
python -m ks1.nightly --inputs /path/to/retained/capture.json --output /tmp/ks1-release-check
```

See `calibration.md` for the parameter, ledger and retry contracts and
`phase4.md` / `phase5.md` for the existing prediction/refresh contracts.

## Nightly audit timing

The nightly gate is 03:00 America/New_York. The existing hourly runner
performs it on the first eligible tick after that time, following fresh result
ingestion; it is not an exact-minute scheduling guarantee. Later finals still
receive hourly catch-up grades. Prediction date partitions and stored lock
identities are unchanged; the cumulative checkpoint date is its processing
date, not a reassignment of games to that date.
