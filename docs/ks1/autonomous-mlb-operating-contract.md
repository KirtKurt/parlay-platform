# Autonomous MLB engineering and research operating contract

Requested by Kurt on September 11, 2026. This contract guides implementation;
it is not evidence that an unattended code-writing agent is already deployed.

## Scope and authority

Continuously operate and improve MLB data collection, coverage, predictions,
immutable pregame evidence, grading, calibration, and research using the
existing repository and AWS owners. Routine reversible, tested changes may
proceed through the established gates without a fresh conversational approval.
Do not alter other sports, place wagers, expose credentials, destroy historical
evidence, weaken qualification gates, or incur materially new unapproved costs.
Do not install a second prediction authority or competing schedule.

Each eligible scheduled MLB game must receive a prediction for each validated,
production-supported market when input integrity permits. Low confidence is
not a reason to omit a forecast. Missing or ambiguous critical identities must
be explicitly reported, not replaced by invented probabilities. Predictions,
confidence, market edge, and actionability are separate outputs. Unsupported
markets are research backlog items, not production capabilities.

## Implemented and verified in this work

The retained failure in GitHub run 34571400992 isolated one 300-second BBS/MLB
start discrepancy for official game 823736. Concurrent PR #715 supplies the
merged fix. Independent duplicate PR #716 was closed, not merged, to preserve
that repair and its scheduled-to-live/final transition handling.

Run 34572484230 produced 15/15 September 11 forecasts and an S3 readback receipt.
Its artifact is 10188345682; its observation timestamp is
2026-09-11T07:03:27.252791+00:00. These were early projected forecasts, not a
claim of all confirmed lineups or final T-10 picks. KS1 serving models remained
frozen; the temperature artifact was unfitted with n=0. A successful publication
is not evidence of improved accuracy, completed grading, or learned calibration.

The accompanying provenance repair adds executable-source and numeric-runtime
fingerprints to the existing research PROTOCOL. The research worker already
uses this protocol in its content-addressed experiment key and retained model
metadata. Identical data and numeric settings must not cause a different
implementation to reuse an old experiment. Existing experiment records remain
untouched. Active and sealed prospective tests retain their existing gates;
this repair does not restart, reopen, or qualify them.

## Remaining acceptance backlog — not implemented by this patch

1. Demonstrate reliable daily grading coverage from immutable official KS1 rows,
   with explicit pending/void/corrected-game handling and source lineage.
2. Retain point-in-time individual starter, available-reliever, and lineup
   fundamentals; distinguish original pregame observations from retrospective
   reconstructions. Test 7/15/30-day windows with adequate sample shrinkage.
3. Run bounded feature additions, removals, and interactions as challengers;
   select using chronological development folds, then an untouched holdout and
   a new prospective test. Never repeatedly optimize against the same test.
4. Establish an unattended research/coding worker with a persistent task queue,
   isolated branches, scoped credentials, bounded retries, explicit spend and
   concurrency ceilings, independent tests, deployment proof, rollback, and an
   emergency stop. A prompt or this Markdown file is not such a worker.
5. Activate validated promotion through the existing authority contract, only
   after its required evidence/review gates. Do not turn off manual first-use
   review merely because the overall directive requests autonomy.
6. Add new markets only after market-specific label, price, settlement, coverage,
   calibration, and holdout/prospective tests exist. Do not label an expected
   total as a validated total-bet probability or winner picks as player props.

## Operational reporting

Report what ran, source/model/code versions, input freshness, expected versus
published coverage, locked versus provisional forecasts, grading completeness,
champion/challenger state, calibration sample size, experiments accepted and
rejected, incident repairs, current bottleneck, and the next scheduled action.
Explicitly distinguish code committed, CI passed, merged, deployed, and
production behavior verified. Never describe a queued or failed job as complete.
