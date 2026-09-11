# Inqsi ARB Autonomous Engineering Controller

Version: `INQSI-ARB-AEC-v2`

The Autonomous Engineering Controller is the bounded control plane above the Inqsi ARB runtime and deployment workflows. It runs every 20 minutes in GitHub Actions, inspects live AWS production plus GitHub release evidence, chooses the highest-priority permitted action, executes at most one bounded coding action per run, and retains controller evidence.

## Autonomous capabilities

- Inspect production health, sport catalog, settlement-rules endpoint and sportsbook inventory.
- Inspect the current `main` SHA and latest ARB deployment and latency-proof conclusions.
- Prioritize production faults above performance or feature work.
- Dispatch one controlled repair when production or the current-main deployment is unhealthy.
- Create a deduplicated engineering blocker when an acceptance gate fails.
- Run the authorized Codex CLI worker against the highest-priority dependency-ready ARB backlog item.
- Independently validate, commit and push Codex output only after the worker has exited.
- Create a draft PR using the Actions token.
- For low-risk application/test increments only, dispatch a trusted exact-head supervisor validation outside Codex's write scope, merge after that validation succeeds, and explicitly dispatch the existing production deployment workflow.
- Preserve decision and release evidence in Actions artifacts.

## Hard boundaries

Codex write scope:

- `inqsi-arb/**`
- `.github/workflows/inqsi-arb-*`

MLB, Tennis, Soccer, KS1, the Engineering Console and unrelated systems are outside the controller's mutation authority. The default action budget is one coding action per 20-minute run. `ARB_AEC_PAUSED=true` is the operator stop control. Unknown actions fail closed.

The controller never places wagers, transfers money, signs into sportsbook accounts, prints credentials, or lowers settlement/freshness/identity/security gates to turn a failure green.

## Credential and publication boundary

The authorized coding worker is the Codex CLI runner at `inqsi-arb/ops/codex_cli_runner.sh`. The workflow maps the GitHub Actions secret `Inqsi_ARB_Autonomous_Coding_Agent` to `OPENAI_API_KEY`, uses an ephemeral `CODEX_HOME`, and authenticates Codex only for that runner lifetime.

The Codex subprocess is deliberately denied GitHub and AWS publication credentials. It may edit only the allowlist and may not push, open pull requests, merge, deploy, or alter remotes. After Codex exits, the supervising wrapper independently validates scope, reruns the complete ARB test suite plus SAM validation/build, normalizes any local Codex commits back to the controller-selected base, and only then restores a short-lived repository credential for the wrapper-owned commit and push.

GitHub Actions unattended draft publication was proven on 2026-09-11 by controller run `34647446309`, which created PR #831 as `github-actions[bot]` from `agent/inqsi-arb-aec-34647446309`. Issue #813 records the resolved publication-policy blocker.

## Trusted autonomous promotion

Autonomous promotion is intentionally **not** performed by the Codex process. The trusted supervisor code lives under `arb_supervisor/**` and the exact-head workflow is `.github/workflows/arb-supervisor-validate.yml`; neither is in the Codex write allowlist.

A draft can be auto-promoted only when all of these hold:

1. the PR is open, draft, unmerged, based on `main`, created by `github-actions[bot]`, and still points to the exact wrapper-pushed SHA;
2. the branch matches `agent/inqsi-arb-aec-<run-id>` and the candidate is a direct child of the controller-selected base;
3. changed files are limited to low-risk `inqsi-arb/src/**`, `inqsi-arb/tests/**`, `ARB_BACKLOG.md` and `ARB_STATUS.md` paths; controller/ops, infrastructure, workflow and supervisor changes are never auto-promoted;
4. the candidate is bounded in size and does not add known wager-execution/deployment/process-execution primitives;
5. no relevant ARB/supervisor change has landed on `main` since the candidate base;
6. a fresh trusted workflow dispatch checks the exact candidate SHA/branch/base, reruns immutable supervisor policy tests, the complete ARB suite and SAM validation/build without AWS or OpenAI credentials;
7. immediately before merge, the PR identity and main-drift checks are repeated;
8. merge is an exact-SHA squash merge through normal GitHub protections; no force/bypass mode is used.

`ARB_AEC_AUTO_PROMOTE` and `ARB_AEC_AUTO_DEPLOY` default to `true` but can be overridden as repository variables. `ARB_AEC_PAUSED=true` remains the global operator stop.

## Autonomous deployment

Merges performed with `GITHUB_TOKEN` are followed by an explicit `workflow_dispatch` of `.github/workflows/inqsi-arb-deploy.yml`; the design does not rely on token-generated push events recursively starting another workflow.

The dispatch carries the exact ARB merge SHA. The deployment workflow requires that SHA to be an ancestor of the dispatched `main` checkout and rejects any later ARB/supervisor drift. It reruns the complete ARB tests, stamps the actual source identity, validates/builds SAM, deploys the isolated `inqsi-arb-prod` stack and runs the existing live health/rules/history/optimizer/persistence/completion/UI/infrastructure verification. The supervisor reports success only when both `test-build` and `deploy-verify` succeed.

If a candidate touches high-risk paths, trusted validation fails, main has relevant drift, GitHub rejects the merge, or deployment verification fails, the automation stops rather than bypassing the gate. The draft/failed evidence remains for review and the next controller cycle can diagnose it.

## Priority order

1. Operator pause.
2. Production health failure.
3. Failed deployment of current main.
4. Failed latency/performance acceptance gate.
5. Missing authorized code-agent runner.
6. Highest-priority dependency-ready item in `ARB_BACKLOG.md`.

## Schedule

The controller workflow is `.github/workflows/inqsi-arb-engineering-controller.yml`. Its schedule is `3,23,43 * * * *` (every 20 minutes), and it can also be manually dispatched or triggered by trusted controller/supervisor changes on `main`.
