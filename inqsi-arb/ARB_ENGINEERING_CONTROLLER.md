# Inqsi ARB Autonomous Engineering Controller

Version: `INQSI-ARB-AEC-v2`

The Autonomous Engineering Controller is the bounded control plane above the Inqsi ARB runtime and deployment workflows. It runs every 20 minutes in GitHub Actions, inspects live AWS production plus GitHub release evidence, chooses the highest-priority permitted action, executes at most one bounded action per run, and retains a controller evidence artifact.

## Autonomous capabilities

- Inspect production health, sport catalog, settlement-rules endpoint and sportsbook inventory.
- Inspect the current `main` SHA and latest ARB deployment and latency-proof conclusions.
- Prioritize production faults above performance or feature work.
- Dispatch one controlled `inqsi-arb-repair.yml` run when production or the current-main deployment is unhealthy.
- Create a deduplicated engineering blocker when an acceptance gate such as latency fails.
- Detect whether an authorized external code-agent runner is configured.
- Hand the next backlog task to that runner only when credential, model and explicit runner command are configured.
- Preserve each decision and result in `arb-engineering-controller-report.json` as a retained Actions artifact.

## Hard boundaries

Allowed mutation scope:

- `inqsi-arb/**`
- `.github/workflows/inqsi-arb-*`

MLB, Tennis, Soccer, KS1 and unrelated systems are outside the controller's mutation authority. The default action budget is one action per 20-minute run. `ARB_AEC_PAUSED=true` is the operator stop control. Unknown actions fail closed.

The controller never places wagers, transfers money, signs into sportsbook accounts, prints credentials, or lowers settlement/freshness/identity/security gates to turn a failure green.

## Code-writing mode

Open-ended feature/code generation requires a separately authorized coding runner. This is deliberately feature-gated rather than simulated.

Configuration contract:

- `OPENAI_API_KEY` GitHub Actions secret (or replace the runner with another approved credential mechanism).
- `ARB_CODE_AGENT_MODEL` repository variable.
- `ARB_CODE_AGENT_COMMAND` repository variable invoking the approved coding runner.

When these are absent, production inspection, bounded repair dispatch, release triage, blocker tracking and evidence collection continue normally. The controller records the missing code-agent capability instead of claiming it is active.

Any attached coding runner must work from current `main`, modify only the ARB allowlist, use an isolated `agent/inqsi-arb-*` branch, run ARB tests and SAM validation/build, open a PR rather than force-push main, preserve all fail-closed/no-wager boundaries, and keep credentials out of output.

## Priority order

1. Operator pause.
2. Production health failure.
3. Failed deployment of current main.
4. Failed latency/performance acceptance gate.
5. Missing authorized code-agent runner.
6. Highest-priority dependency-ready item in `ARB_BACKLOG.md`.

## Deployment

The workflow is `.github/workflows/inqsi-arb-engineering-controller.yml`. Its schedule is `3,23,43 * * * *` (every 20 minutes). It can also be manually dispatched. Each run first executes the controller guardrail test suite before obtaining AWS evidence or mutating GitHub state.
