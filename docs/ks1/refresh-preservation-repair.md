# KS1 refresh and publication preservation repair

## Retained production evidence

- [Watchdog 34648964859](https://github.com/KirtKurt/parlay-platform/actions/runs/34648964859)
  stopped at 2026-09-11 21:42:25 UTC on a workflow-runs GET failure. The
  subprocess error escaped the polling loop before dispatch or owner handoff.
  The retained log does not identify the HTTP failure category.
- [Publication 34627919309](https://github.com/KirtKurt/parlay-platform/actions/runs/34627919309)
  retained Cubs game 824631 at 17:31:04 UTC, with first pitch 18:20 UTC and
  stored T-10 at 18:10 UTC. Official `p_home` and `p_raw` were both
  0.5613444901815502.
- [Publication 34630837783](https://github.com/KirtKurt/parlay-platform/actions/runs/34630837783)
  removed 824631 at 18:02:24 UTC with `not_scheduled_before_T10`, before that
  stored cutoff. Its previous-input hash matches the prior publication hash.
  Its retained lineup cache has no entry for the game. Successful runs did not
  retain the complete official schedule, so the exact status versus revised-time
  trigger cannot be distinguished retrospectively. Both paths reproduce the
  same removal defect in tests.

## Repair

GET requests receive at most three 10-second attempts with 2/5-second backoff.
An exhausted read skips dispatch and permits the bounded owner to keep checking.
A recovered poll uses a fresh workflow-enabled check and run list. Persistent
read failures remain visible as a failed job, after a handoff only when a fresh
enabled check permits it. POSTs remain single-attempt, including timeouts;
ambiguous ingestion dispatches retain the one-hour local cooldown. Disabled
workflows are not re-enabled. The 55-minute owner deadline and 60-minute job
limit remain unchanged.

Daily inference refuses unexpected loss of any previously published pick.
Explicit pre-cutoff postponements/cancellations can still withdraw a game and
are recorded separately. Existing post-cutoff rows retain their stored values,
including `p_raw`, `p_home`, timestamps and lineup evidence. The publication
boundary independently validates the actual stored rows before any sidecar or
prediction write; existing conditional ETag writes still protect concurrent
publication. Successful reports now retain schedule status/time observations;
failed runs already retain captured schedule identities.

An unexpected pre-cutoff status transition fails that publication and leaves
the previous stored slate intact. It does not create a new lock, change the
cutoff or admit post-start model inputs. A subsequent run can use the unchanged
pre-cutoff publication under the existing T-10 rules. This may temporarily delay
other updates during a source inconsistency; silent pick deletion is rejected.

Historical missing rows are not restored and no grades are fabricated. This
repair does not change models, calibration, lock eligibility, vendors or other
engines. Candidate selection and temperature fitting remain separate.

## Regression coverage

Tests cover retry exhaustion/recovery, redaction, ambiguous POST timeout,
disabled and unavailable workflow checks, persistent outage handoff, early
live/final/revised-start transitions, legitimate withdrawals, and rejection of
frozen-row deletion or mutation before any storage writes. Existing KS1 tests
continue to exercise idempotency, ETags, startup calibration and frozen values.
