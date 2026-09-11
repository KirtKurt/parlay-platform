# Inqsi ARB Autonomous Engineering Controller

Version: `INQSI-ARB-AEC-v2`

## Purpose

The Autonomous Engineering Controller (AEC) is the bounded control plane above the Inqsi ARB runtime and deployment workflows. It runs every 20 minutes in GitHub Actions, inspects live AWS production plus GitHub release evidence, chooses the highest-priority permitted action, executes at most one bounded action per run, and preserves an evidence artifact.

It is deliberately separate from the betting product. It never places wagers, transfers money, signs into sportsbook accounts, or weakens settlement/freshness/identity gates.

## What it can do autonomously

- Inspect the production health, sport catalog, rules endpoint and sportsbook inventory.
- Inspect the current `main` SHA and the latest ARB deploy/latency workflow conclusions.
- Prioritize production failure above performance or feature work.
- Dispatch exactly one controlled `inqsi-arb-repair.yml` repair/redeploy when production or the latest main deployment is unhealthy.
- Open a deduplicated engineering issue when an acceptance gate such as production latency fails.
- Detect whether an authorized external code-agent runner is configured.
- Hand a backlog task to that runner only when both the credential/model and an explicit runner command are configured.
- Record the selected action, evidence, guardrails and result as a retained GitHub Actions artifact.

## Hard guardrails

The AEC is scoped to:

- `inqsi-arb/**`
- `.github/workflows/inqsi-arb-*`

It must not mutate MLB, Tennis, Soccer, KS1 or other prediction systems. The default action budget is one mutation/dispatch per 20-minute run. `ARB_AEC_PAUSED=true` is the operator stop control. Unknown actions fail closed.

Production correctness gates are not tunable by the controller. A failing latency, settlement, identity, freshness or security gate is recorded/repaired; the controller is not allowed to lower the gate merely to obtain a green result.

## Code-writing mode

Arbitrary feature/code generation requires an independently authorized coding runner. This is intentionally feature-gated rather than faked.

Required configuration:

- `OPENAI_API_KEY` GitHub Actions secret (or another approved agent credential if the runner is changed).
- `ARB_CODE_AGENT_MODEL` repository variable.
- `ARB_CODE_AGENT_COMMAND` repository variable pointing to the approved runner command.

If those are absent, the AEC continues production inspection, repair dispatch, release triage and blocker tracking, and creates one deduplicated blocker issue. It does not claim that autonomous arbitrary code authoring is active.

Any coding runner attached to this contract must:

1. work from current `main`;
2. modify only the allowlisted ARB paths;
3. create an isolated `agent/inqsi-arb-*` branch;
4. run the ARB test suite and SAM validation/build;
5. open a PR rather than force-push `main`;
6. preserve fail-closed behavior and no-wager-placement boundaries;
7. allow normal CI/branch controls to decide promotion;
8. never print credentials.

## Controller precedence

1. Operator pause.
2. Production health failure.
3. Failed deployment of current main.
4. Failed latency/performance acceptance gate.
5. Missing authorized code-agent runner.
6. Highest-priority dependency-ready ARB backlog item.

## Evidence

Every scheduled/manual run uploads `arb-engineering-controller-report.json` for 90 days. The report includes the main SHA, production state, observed sport/book counts when available, latest deploy/latency conclusions, planned action, result, and guardrail state.

This controller complements the existing runtime production controller; it does not replace low-level health monitoring or AWS alarms.
