# MLB player research release — 2026-09-09

The user authorized deployment of all pending MLB work. The previously unpublished
player-signal branch was unavailable after the workspace reverted; this release
rebuilds the expansion against current production source rather than claiming the
lost local commit was deployed.

## Included

- Separate MLB research Lambda, invoked by the existing canonical training and
  capture owner; scoped versioned S3 storage and independent execution leases.
- All active roster pitchers (including other starters and two-way players) and
  all roster batters, with confirmed batting order and 7/15/30 ET-day statistics.
  ERA uses outs, not decimal innings. Missing game logs remain incomplete.
- Starter and pooled bullpen ERA, WHIP and K-BB; batter AVG/OBP/SLG/OPS/ISO/K/BB;
  short-window trends; 1/3/5-day pitching workload including completed same-day
  games; observed platoon matchup, lineup changes, market movement and reversals.
- Checked Statcast contacts, velocity and pitch mix; source-coverage masks;
  roof-aware weather, transaction activity, rest/travel and recent venue scoring.
- Hourly official-source and Statcast preparation; existing historical archive
  publication; original whole-slate settlement; one conditional dataset publisher.
- Fixed market-offset linear, shallow tree and Poisson-run development searches.
  Whole-slate chronological folds, a later development holdout, then a separate
  immutable future test. Prefer original-only development once 600 games over
  40 slates exist; retain a rolling whole-slate window so old missing features do
  not suppress new observations indefinitely.
- Recover missing predictions from saved snapshots before T10; never write late.
  A missing final test slate seals an unsuccessful test. New game identities can
  start a distinct candidate after a failed test; old seals remain immutable.
- Freshness-aware health and read-only post-deployment code/execution proof.

## Verification before repository CI

- Final production-source regression collection: **1,882 passed** in 135.49
  seconds. The signed primary training status remains unchanged by dispatch.
- Canonical schedule/authority invariants pass. Deployment transforms are
  source-idempotent. Workflow YAML, compile and whitespace checks pass.
- Live MLB schedule, completed feed and Statcast schema checks passed.
- Public-source verification completed at 2026-09-09 23:39:42 UTC: 409 prior
  completed-game sources, zero source failures. The one remaining eligible
  pregame (823900) has both complete 28-player active rosters, all 29 pitchers
  including its two-way starter, and both nine-player lineups. Pitcher and
  lineup batting windows are complete at 7, 15 and 30 days. Exact rates and the
  observation fingerprint are in `mlb_research_source_summary_20260909.json`.
- Current production pulse at 23:20 UTC reports healthy existing successor
  training/capture and zero recent MLB AUTO Lambda errors, while correctly
  retaining `NO_QUALIFIED_CHAMPION` and zero official picks. This is existing
  production evidence, not proof that this pending release has been deployed.

## Publication and pending-work audit

The user explicitly authorized publication to the public
`KirtKurt/parlay-platform` repository and deployment to `parlay-platform-dev`.
PR #690 contains this release. The Lambda packaging gate identified a NumPy
wheel incompatibility; NumPy is now pinned to 2.2.6, and verification uses the
same research requirements as the deployed worker. The compatible Python 3.11
manylinux2014 wheel was downloaded successfully, and all 40 research tests
pass with the pinned dependency.

The older pending deployment/quarantine proposals (#597, #601, #604) already
have their required behavior in current main. Temporary diagnosis-only drafts
and retired R6/R7 recovery proposals are not reactivated. The still-applicable
bounded candidate-review error diagnostic from #623 was missing and is included
here with its four regression cases. It changes error reporting only.

AWS deployment and natural-execution verification are tracked in PR #690's
workflow runs; packaging or local-test success alone is not deployment proof.

## Release boundaries

No R8 frozen test or immutable historical prediction is reopened or rewritten.
Reconstructed history never becomes an original pregame observation. This worker
has no serving, promotion, DynamoDB prediction-write or wagering permissions.
No Tennis or Soccer behavior is changed. Deployment success is distinct from a
qualified model or complete source coverage; the deployed proof reports both.
