# KS1 hourly refresh and nightly source-order repair

## Observed problem

The September 10 production publications at 13:00, 15:42, 18:37 and 21:08
Eastern completed successfully, but arrived hours apart. The last observed
ingestion job in run [34549267972](https://github.com/KirtKurt/parlay-platform/actions/runs/34549267972)
finished in roughly two minutes; it was not a long-running worker blocking the
hourly concurrency group. At the September 11 inspection, no later KS1 run was
created even though other workflows were running. The underlying reason GitHub
did not deliver these cron events is not established. Moving the cron to :17
did not establish hourly execution.

The workflow also invoked nightly grading **before** refreshing the retained
official results. A delayed 01:00 run could seal that date's write-once ledger
using the previous refresh's incomplete set of finals, then skip further
attempts that day. This is an ordering defect/risk, not a claim that any
unobserved game was graded incorrectly.

## Repair (PR only until approved)

- Keep the existing main ingestion workflow as the only prediction/ledger
  writer and keep its :17 hourly cron.
- Add a separate KS1-only dispatch watchdog. A singleton owner checks the
  exact ingestion workflow once a minute, suppresses dispatch while a main
  production run is active, and requests a main run when its last attempt
  started at least 60 minutes ago. PRs, forks and other engines do not count.
- The owner runs for at most 55 minutes then requests its successor. A :07
  cron and a narrowly scoped main push are recovery seeds. This avoids making
  continuity depend solely on cron delivery, without AWS resources or another
  data provider. The owner has only GitHub contents-read/actions-write, and
  cannot access AWS credentials, predictions or models.
- Refresh the existing retained source dataset before nightly grading. Verify
  complete game-source coverage and that both the official schedule receipt
  and source-update timestamp fall within this ingestion attempt. A stale
  LEASE_BUSY cache, partial finals capture or future receipt cannot seal a
  nightly checkpoint. Failures retain a diagnostic artifact with zero ledger
  writes. Already-completed/not-yet-due checks retain a skip report.
- Keep grading failures red, but allow the independent daily refresh to run
  after a successful source-ingestion command. Do not bypass daily input or
  publication validation.

The ledger format, once-per-night due rule, T-10 admission/preservation,
official p_home/p_raw values, model references, temperature gates and previous
checkpoints are unchanged. No full-model training or evolution loop is added.
No other sport or engine's workflow is modified.

## Limits and operating cost

This uses roughly one continuously occupied GitHub-hosted runner (including
idle waiting), about 24 runner-hours/day, in addition to ingestion. Repository
Actions billing/quotas apply. It is not a wall-clock SLA: unavailable runners,
API failures or delayed dispatch can still delay a refresh. Existing ingestion
concurrency serializes a cron/dispatch race; the watchdog does not cancel a
production run, bypass a lock, force publication or retry ambiguous POSTs in a
tight loop. Failed production attempts are retried no more than hourly by the
watchdog. Disable the watchdog workflow and cancel its active/pending owners
to stop it immediately; a disabled target/owner is never re-enabled by code.

## Verification and activation boundary

Offline regression tests cover stale/missing/future finals, complete empty
slates, immutable official grading, failed writes/readback, daily independence,
hourly thresholds, active-run suppression, fork/PR exclusion, re-run timestamps,
ambiguous dispatch cooldown, disabled workflows, and bounded handoff.

No production recovery is claimed from local/PR tests. After separate approval
to merge/activate, verify the initial main run's retained daily publication and
S3 readback, its nightly ledger/calibration artifact, and at least two hourly
successor refreshes. Inspect official grades only from retained prospective
locks and final-source receipts; never reconstruct missing picks. The first
main source refresh must precede the overdue nightly checkpoint. Do not rerun
an old pre-repair source SHA as a substitute for activating this repair.

GitHub documents that [scheduled events may be delayed or dropped](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
and that [workflow_dispatch can be triggered with GITHUB_TOKEN](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow).
