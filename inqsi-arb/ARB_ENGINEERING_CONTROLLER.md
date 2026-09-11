# Inqsi ARB Autonomous Engineering Controller

Version: `INQSI-ARB-AEC-v2` (release continuation candidate; not yet deployed)

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
- For bounded non-executable status/backlog documents only, and only after ordinary PR Actions approvals and CI are satisfied, dispatch a trusted exact-head supervisor validation outside Codex's write scope, merge after that validation succeeds, and explicitly dispatch the existing production deployment workflow.
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
3. changed files are limited to regular UTF-8 `inqsi-arb/ARB_BACKLOG.md` and `inqsi-arb/ARB_STATUS.md` documents. All source, tests, hooks, dependencies, build metadata, controller/ops, infrastructure, workflow and supervisor changes require ordinary review. The prior source denylist did not establish a production credential boundary;
4. the candidate has exactly one parent, at most 600 changed lines, at most 64 KiB per file and 128 KiB total; symlinks, executable files, binary bodies and document deletions are rejected. Both sides of renames are checked;
5. no relevant ARB/supervisor change has landed on `main` since the candidate base;
6. ordinary exact-head PR CI has passed, including the actual test-build job. `action_required`, missing or failed evidence blocks before dispatch. A uniquely identified main workflow then validates the candidate as data in a separate checkout; policy, pytest hooks, tests and build inputs come from the immutable trusted workflow revision, never candidate files. The built artifact is size-bounded and no AWS/OpenAI credentials are present;
7. immediately before merge, the PR identity and main-drift checks are repeated;
8. merge is an exact-SHA squash merge through normal GitHub protections. The returned merge parent and ARB tree are checked before deployment; a base race leaves the merge recorded but blocks deployment for reconciliation. No force/bypass mode is used.

`ARB_AEC_AUTO_PROMOTE` and `ARB_AEC_AUTO_DEPLOY` default to `false` until the bootstrap is reviewed and live-qualified. Repository variables can enable the bounded document path after those gates are satisfied; source/test changes remain ineligible regardless of these flags. `ARB_AEC_PAUSED=true` remains the global operator stop.

## Autonomous deployment

Merges performed with `GITHUB_TOKEN` are followed by an explicit `workflow_dispatch` of `.github/workflows/inqsi-arb-deploy.yml`; the design does not rely on token-generated push events recursively starting another workflow.

Every deployment dispatch requires the exact reviewed ARB merge SHA. Supervisor dispatches also carry a unique nonce; successful receipt acceptance binds the workflow path, main branch, trusted main SHA, creation time and nonce, and rechecks those fields on the pinned run ID. The deployment workflow requires that SHA to be an ancestor of the dispatched `main` checkout and rejects any later ARB/supervisor drift. It reruns the complete ARB tests, stamps the actual source identity, validates/builds SAM before AWS credentials are configured, deploys the isolated `inqsi-arb-prod` stack and runs the existing live health/rules/history/optimizer/persistence/completion/UI/infrastructure verification. The supervisor reports success only when both `test-build` and `deploy-verify` succeed.

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

## Outstanding qualification

PR #831 proves unattended draft creation, but its Actions runs require approval. A replacement workflow dispatch is not approval and must not substitute for that gate. This continuation has not established autonomous application release, production credential/egress isolation, live BBD entitlements, a completed 24-hour acceptance window, or complete v3 market coverage. Source/test increments remain draft proposals until a separately reviewed execution and release boundary supports them.

The controller wrapper budget is 120 minutes within a 130-minute job. Ordinary PR CI can wait up to 20 minutes; an explicit Actions approval requirement stops immediately. Validation permits 15 minutes of execution with 25 minutes of supervisor waiting; deployment permits 15 minutes of tests plus 30 minutes of deployment with 50 minutes of waiting. Queue/discovery delays and final timeouts remain explicit failures, not false successful releases.
