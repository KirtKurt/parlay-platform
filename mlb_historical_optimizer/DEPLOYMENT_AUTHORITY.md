# MLB historical optimizer deployment authority

The only workflow authorized to deploy the `parlay-platform-mlb-historical-optimizer` CloudFormation stack is `.github/workflows/mlb-historical-v7-recovery.yml`.

Scheduled status and watchdog workflows are runtime observers and bounded invokers only. They must not run `sam deploy`, alter `DeployGitSha`, or override the canonical historical range, credit ceiling, handler, or range-extension parameters.

Canonical runtime contract:

- Handler: `mlb_historical_optimizer_v7_recovery_entrypoint.lambda_handler`
- Historical end date: `2026-07-24`
- Maximum historical credits: `300000`
- Range extension authorized: `true`
- Feature rematerialization must be complete with no unresolved errors before normal backfill continues.
