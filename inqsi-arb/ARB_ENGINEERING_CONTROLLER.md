# Inqsi ARB Autonomous Engineering Controller

Version: `INQSI-ARB-AEC-v2`

The Autonomous Engineering Controller is the bounded control plane above the Inqsi ARB runtime and deployment workflows. It runs every 20 minutes in GitHub Actions, inspects live AWS production plus GitHub release evidence, chooses the highest-priority permitted action, executes at most one bounded action per run, and retains a controller evidence artifact.

## Autonomous capabilities

- Inspect production health, sport catalog, settlement-rules endpoint and sportsbook inventory.
- Inspect the current `main` SHA and latest ARB deployment and latency-proof conclusions.
- Prioritize production faults above performance or feature work.
- Dispatch one controlled `inqsi-arb-repair.yml` run when production or the current-main deployment is unhealthy.
- Create a deduplicated engineering blocker when an acceptance gate such as latency fails.
- Detect whether the authorized Codex CLI coding runner is configured.
- Hand the next backlog task to that runner only when its credential and explicit runner command are configured.
- Preserve each decision and result in `arb-engineering-controller-report.json` as a retained Actions artifact.

## Hard boundaries

Allowed mutation scope:

- `inqsi-arb/**`
- `.github/workflows/inqsi-arb-*`

MLB, Tennis, Soccer, KS1 and unrelated systems are outside the controller's mutation authority. The default action budget is one action per 20-minute run. `ARB_AEC_PAUSED=true` is the operator stop control. Unknown actions fail closed.

The controller never places wagers, transfers money, signs into sportsbook accounts, prints credentials, or lowers settlement/freshness/identity/security gates to turn a failure green.

## Code-writing mode

The authorized coding worker is the Codex CLI runner at `inqsi-arb/ops/codex_cli_runner.sh`, invoked by the controller workflow. The workflow maps the GitHub Actions secret `Inqsi_ARB_Autonomous_Coding_Agent` to `OPENAI_API_KEY`, uses an ephemeral `CODEX_HOME`, and authenticates Codex only for that runner lifetime.

The Codex subprocess is deliberately denied GitHub and AWS publication credentials. It may edit only the ARB allowlist and may not push, open pull requests, merge, deploy, or alter remotes. After Codex exits, the supervising wrapper independently validates scope, reruns the complete ARB test suite plus SAM validation/build, normalizes any local Codex commits back to the controller-selected base, and only then restores a short-lived repository credential for the wrapper-owned commit and push.

The wrapper requests a DRAFT pull request after the exact validated bytes are pushed. Repository policy must allow GitHub Actions to create pull requests. Enabling that policy grants no automatic approval, merge, deployment, or wager authority. A fresh controller run must prove unattended draft creation before the publication blocker is considered closed.

Any coding increment must start from current `main`, use an isolated `agent/inqsi-arb-*` branch, preserve all fail-closed/no-wager boundaries, and keep credentials out of output and committed files.

## Priority order

1. Operator pause.
2. Production health failure.
3. Failed deployment of current main.
4. Failed latency/performance acceptance gate.
5. Missing authorized code-agent runner.
6. Highest-priority dependency-ready item in `ARB_BACKLOG.md`.

## Deployment

The workflow is `.github/workflows/inqsi-arb-engineering-controller.yml`. Its schedule is `3,23,43 * * * *` (every 20 minutes). It can also be manually dispatched. Each run first executes the controller guardrail test suite before obtaining AWS evidence or mutating GitHub state.

## Unattended publication proof

On 2026-09-11 the repository supervisor enabled GitHub Actions' repository setting allowing Actions to create pull requests. This documentation update intentionally triggers a fresh controller run from a new `main` commit so the setting is validated end-to-end with a new run ID and branch. Success requires the Actions token itself to create a new open DRAFT PR whose head SHA exactly matches the wrapper-pushed commit. A PR opened by a connected human account does not satisfy this proof.
