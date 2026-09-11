# MLB successor deployment identity health repair

## Observed failure

The live September 11 pulse reports successor training health FAILED with `latest_status_deployment_identity_mismatch` after a repository deployment, while current MLB capture continues successfully and no T-10 misses are reported. The trainer health contract currently compares the latest persisted run's global deploy Git SHA against the currently deployed global Git SHA. Unrelated repository deploys can therefore invalidate an otherwise unchanged MLB implementation until the next training heartbeat.

## Required repair

Keep the health check fail-closed for actual MLB implementation changes, but bind compatibility to MLB-specific executable lineage rather than an unrelated repository-head change alone.

The accepted identity must include deterministic hashes covering the MLB trainer/research implementation and infrastructure contract needed by that runtime. A prior heartbeat may be considered compatible only when those MLB-specific hashes match the deployed runtime and the heartbeat is within its existing age bound. A Git SHA mismatch must still fail when the MLB-specific implementation identity cannot prove equivalence.

Do not relax staleness, status-ok, experiment, lease, manifest, source, qualification, or promotion checks. Do not activate a champion, change probabilities, modify locks, or alter other sports.

## Regression cases

1. Same MLB implementation/template identity, unrelated repo Git SHA changed: health remains valid if all other checks pass.
2. MLB trainer source hash changed: health fails even if a stale heartbeat is otherwise fresh.
3. MLB infrastructure/template contract hash changed: health fails.
4. Missing implementation identity: fail closed.
5. Stale heartbeat: fail as before.
6. Latest run not ok: fail as before.
7. Selection-capture and training modes retain independent lease/age semantics.

## Current data-side evidence

Separately, the September 10 research ingestion partial state has two honest exclusions/errors that must not be papered over: Statcast `COVERAGE_MISMATCH` (5 expected games, 0 observed) and `MISSING_T10_SNAPSHOTS` for four game IDs. The latter cannot be retrospectively backfilled. Current September 11 capture cadence is every two minutes and reports no missed T-10 games.
