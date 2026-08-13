# MLB V7-V10 Retirement

Effective August 13, 2026, MLB algorithms V7, V8, V9, and V10 are retired by owner directive.

## Disabled

- Scheduled and manual GitHub Actions entry points for V7-V10
- V7-V10 hourly and status reporting
- Training, backfill, simulation, observational shadow, validation, recovery, and promotion workflows
- Matching AWS EventBridge rules and Scheduler schedules
- Matching Lambda execution through reserved concurrency set to zero
- Matching in-flight Step Functions executions
- Matching CloudWatch alarm actions

## Preserved

- MLB Auto and its shared production collection/scoring infrastructure
- Tennis
- Soccer
- Historical source code and prior evidence in Git history for audit purposes

Reactivation requires a new explicit owner directive and must not occur through an automated repair or recovery path.
