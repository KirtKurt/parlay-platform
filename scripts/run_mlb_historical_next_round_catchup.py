#!/usr/bin/env python3
"""Run a readiness-aware, bounded V7 next-round catch-up.

This controller never treats an honest settled-horizon wait as a failure and never
forces an optimization before the canonical evidence target is reached. It verifies
the deployed ceiling dynamically, requires complete feature rematerialization, and
only demands a new experiment when the durable state says the next round is ready.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any, Mapping

import boto3

try:
    from scripts import run_mlb_historical_watchdog as watchdog
except ModuleNotFoundError:  # Direct execution from the scripts directory.
    import run_mlb_historical_watchdog as watchdog


VERSION = "MLB-HISTORICAL-NEXT-ROUND-CATCHUP-v2-readiness-aware"
WAITING_FOR_EVIDENCE = "WAITING_FOR_SETTLED_EVIDENCE"
RUN_NEXT_OPTIMIZATION = "RUN_NEXT_OPTIMIZATION"
ADVANCE_ACTIVE_PIPELINE = "ADVANCE_ACTIVE_PIPELINE"
CHAMPION_WAITING = "CHAMPION_WAITING_FOR_NEW_EVIDENCE"


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def summarize_state(state: Mapping[str, Any]) -> dict[str, Any]:
    latest = state.get("latestExperiment") or {}
    if not isinstance(latest, Mapping):
        latest = {}
    gate = latest.get("promotionGate") or {}
    if not isinstance(gate, Mapping):
        gate = {}
    overfit = gate.get("overfitChecks") or {}
    if not isinstance(overfit, Mapping):
        overfit = {}
    eligible = _int(state.get("eligibleGameCount"))
    target = _int(state.get("targetSettledGames"))
    return {
        "phase": state.get("phase"),
        "currentDate": state.get("currentDate"),
        "currentSlotIndex": _int(state.get("currentSlotIndex")),
        "endDate": state.get("endDate"),
        "eligibleGameCount": eligible,
        "completeSlateCount": _int(state.get("completeSlateCount")),
        "featureRematerializedSlateCount": _int(
            state.get("featureRematerializedSlateCount")
        ),
        "featureRematerializationTotalSlateCount": _int(
            state.get("featureRematerializationTotalSlateCount")
        ),
        "targetSettledGames": target,
        "gamesUntilNextOptimization": max(0, target - eligible),
        "nextOptimizationReady": bool(
            state.get("nextOptimizationReady") is True
            or (target > 0 and eligible >= target)
        ),
        "optimizationRound": _int(state.get("optimizationRound")),
        "optimizationCompletedAtUtc": state.get(
            "optimizationCompletedAtUtc"
        ),
        "experimentId": latest.get("experimentId"),
        "candidateStatus": latest.get("status"),
        "trainingGameCount": _int(gate.get("trainingGameCount")),
        "walkForwardMeanDailyAccuracy": gate.get(
            "walkForwardMeanDailyAccuracy"
        ),
        "untouchedHoldoutMeanDailyAccuracy": gate.get(
            "untouchedHoldoutMeanDailyAccuracy"
        ),
        "brierDeltaVsBaseline": overfit.get("brierDeltaVsBaseline"),
        "logLossDeltaVsBaseline": overfit.get(
            "logLossDeltaVsBaseline"
        ),
        "promotionPassed": gate.get("passed") is True,
        "networkRequestCount": _int(state.get("networkRequestCount")),
        "creditsConsumed": _int(state.get("creditsConsumed")),
        "revision": _int(state.get("revision")),
        "freshAuditExpansionRequired": (
            state.get("freshAuditExpansionRequired") is True
        ),
        "freshAuditCollectedDayCount": _int(
            state.get("freshAuditCollectedDayCount")
        ),
        "freshAuditCollectedGameCount": _int(
            state.get("freshAuditCollectedGameCount")
        ),
        "lastError": state.get("lastError"),
    }


def classify_state(
    state: Mapping[str, Any], *, expected_ceiling: str
) -> str:
    common = watchdog.validate_common_state(
        state, expected_ceiling=expected_ceiling
    )
    complete = _int(state.get("completeSlateCount"))
    rematerialized = _int(
        state.get("featureRematerializedSlateCount")
    )
    total = _int(state.get("featureRematerializationTotalSlateCount"))
    if complete and (rematerialized != complete or total != complete):
        raise ValueError(
            "feature_rematerialization_does_not_cover_completed_slates"
        )

    summary = summarize_state(state)
    phase = str(common.get("phase") or "")
    if phase == watchdog.WAITING_PHASE:
        watchdog.validate_waiting_state(
            state, expected_ceiling=expected_ceiling
        )
        return (
            RUN_NEXT_OPTIMIZATION
            if summary["nextOptimizationReady"]
            else WAITING_FOR_EVIDENCE
        )
    if summary["nextOptimizationReady"]:
        return RUN_NEXT_OPTIMIZATION
    if phase in watchdog.ACTIVE_PHASES:
        return ADVANCE_ACTIVE_PIPELINE
    if phase == "PROMOTED":
        return CHAMPION_WAITING
    if phase == "CANDIDATE_REJECTED":
        return WAITING_FOR_EVIDENCE
    raise ValueError(f"unsupported_next_round_phase:{phase}")


def _runtime(
    *,
    region: str,
    stack_name: str,
    expected_handler: str,
    expected_max_credits: str,
    template: Path,
) -> tuple[Any, str, str, dict[str, Any]]:
    expected_ceiling = watchdog.canonical_end_date(template)
    cf = boto3.client("cloudformation", region_name=region)
    lam = boto3.client("lambda", region_name=region)
    stack = (
        cf.describe_stacks(StackName=stack_name).get("Stacks") or []
    )[0]
    stack_status = str(stack.get("StackStatus") or "")
    if stack_status not in watchdog.STABLE_STACK_STATUSES:
        raise RuntimeError(
            f"historical_stack_not_stable:{stack_status}"
        )
    function_name = watchdog._stack_outputs(stack).get(
        "HistoricalOptimizerFunctionName"
    )
    if not function_name:
        raise RuntimeError(
            "historical_optimizer_function_output_missing"
        )
    config = lam.get_function_configuration(
        FunctionName=function_name
    )
    environment = (
        (config.get("Environment") or {}).get("Variables") or {}
    )
    checks = {
        "handler": config.get("Handler") == expected_handler,
        "configuredCeiling": environment.get(
            "MLB_HISTORICAL_END_DATE"
        )
        == expected_ceiling,
        "maxCredits": environment.get(
            "MLB_HISTORICAL_MAX_CREDITS"
        )
        == str(expected_max_credits),
        "rangeExtensionAuthorized": environment.get(
            "MLB_HISTORICAL_RANGE_EXTENSION_AUTHORIZED"
        )
        == "true",
    }
    if not all(checks.values()):
        raise RuntimeError(
            f"historical_runtime_configuration_mismatch:{checks}"
        )
    return (
        lam,
        function_name,
        expected_ceiling,
        {
            "functionName": function_name,
            "stackStatus": stack_status,
            "handler": config.get("Handler"),
            "deployGitSha": environment.get("INQSI_DEPLOY_GIT_SHA"),
            "configuredCeilingDate": expected_ceiling,
            "maximumCredits": environment.get(
                "MLB_HISTORICAL_MAX_CREDITS"
            ),
            "rangeExtensionAuthorized": environment.get(
                "MLB_HISTORICAL_RANGE_EXTENSION_AUTHORIZED"
            ),
            "checks": checks,
        },
    )


def _experiment_advanced(
    baseline: Mapping[str, Any], current: Mapping[str, Any]
) -> bool:
    baseline_summary = summarize_state(baseline)
    current_summary = summarize_state(current)
    return bool(
        current_summary["experimentId"]
        and current_summary["experimentId"]
        != baseline_summary["experimentId"]
        or current_summary["optimizationRound"]
        > baseline_summary["optimizationRound"]
        or (
            current_summary["optimizationCompletedAtUtc"]
            and current_summary["optimizationCompletedAtUtc"]
            != baseline_summary["optimizationCompletedAtUtc"]
        )
    )


def run(
    *,
    region: str,
    stack_name: str,
    expected_handler: str,
    expected_max_credits: str,
    template: Path,
    max_attempts: int = 4,
) -> dict[str, Any]:
    lam, function_name, ceiling, runtime_identity = _runtime(
        region=region,
        stack_name=stack_name,
        expected_handler=expected_handler,
        expected_max_credits=expected_max_credits,
        template=template,
    )
    baseline_response = watchdog._status(
        lam, function_name, "next_round_baseline"
    )
    baseline = dict(baseline_response["state"])
    initial_action = classify_state(
        baseline, expected_ceiling=ceiling
    )
    action = initial_action
    baseline_authority = baseline_response.get("productionAuthority")
    progress: list[dict[str, Any]] = []
    final = baseline
    new_experiment = False
    blockers: list[str] = []

    if action in {WAITING_FOR_EVIDENCE, CHAMPION_WAITING}:
        pass
    else:
        attempts = max(1, max_attempts) if action == RUN_NEXT_OPTIMIZATION else 1
        previous = baseline
        for attempt in range(1, attempts + 1):
            response = watchdog._invoke(
                lam,
                function_name,
                {
                    "mode": "orchestrate",
                    "run": f"github_next_round_catchup_{attempt}",
                },
            )
            if response.get("ok") is not True:
                raise RuntimeError(
                    f"historical_optimizer_orchestrate_failed:{response}"
                )
            if response.get("status") == "LEASE_HELD":
                progress.append(
                    {
                        "attempt": attempt,
                        "observedAtUtc": datetime.now(
                            timezone.utc
                        ).isoformat(),
                        "status": "LEASE_HELD",
                    }
                )
                time.sleep(15)
                continue

            status_response = watchdog._status(
                lam,
                function_name,
                f"next_round_after_{attempt}",
            )
            current = dict(status_response["state"])
            if status_response.get("productionAuthority") != baseline_authority:
                raise RuntimeError("production_authority_changed_during_catchup")
            current_action = classify_state(
                current, expected_ceiling=ceiling
            )
            if str(current.get("phase") or "") in watchdog.ACTIVE_PHASES:
                watchdog.validate_transition(
                    previous,
                    current,
                    expected_ceiling=ceiling,
                    published=baseline,
                    lease_held=False,
                )
            progress.append(
                {
                    "attempt": attempt,
                    "observedAtUtc": datetime.now(
                        timezone.utc
                    ).isoformat(),
                    "action": current_action,
                    **summarize_state(current),
                }
            )
            final = current
            previous = current
            if _experiment_advanced(baseline, current):
                new_experiment = True
                break
            if current_action in {
                WAITING_FOR_EVIDENCE,
                CHAMPION_WAITING,
            }:
                action = current_action
                break
            time.sleep(5)

        final_action = classify_state(
            final, expected_ceiling=ceiling
        )
        if (
            final_action == RUN_NEXT_OPTIMIZATION
            and not new_experiment
        ):
            blockers.append(
                "next_optimization_did_not_complete_within_bound"
            )

    baseline_summary = summarize_state(baseline)
    final_summary = summarize_state(final)
    return {
        "proofType": "MLB_HISTORICAL_NEXT_ROUND_CATCHUP",
        "version": VERSION,
        "createdAtUtc": datetime.now(timezone.utc).isoformat(),
        "ok": not blockers,
        "runtimeIdentity": runtime_identity,
        "initialAction": initial_action,
        "finalAction": classify_state(
            final, expected_ceiling=ceiling
        ),
        "baseline": baseline_summary,
        "final": final_summary,
        "movement": {
            "eligibleGames": (
                final_summary["eligibleGameCount"]
                - baseline_summary["eligibleGameCount"]
            ),
            "completeSlates": (
                final_summary["completeSlateCount"]
                - baseline_summary["completeSlateCount"]
            ),
            "networkRequests": (
                final_summary["networkRequestCount"]
                - baseline_summary["networkRequestCount"]
            ),
            "optimizationRounds": (
                final_summary["optimizationRound"]
                - baseline_summary["optimizationRound"]
            ),
        },
        "newExperimentCompleted": new_experiment,
        "waitingIsNotFailure": (
            classify_state(
                final, expected_ceiling=ceiling
            )
            in {WAITING_FOR_EVIDENCE, CHAMPION_WAITING}
        ),
        "productionAuthorityChanged": False,
        "promotionGateChanged": False,
        "progressSamples": progress[-12:],
        "blockers": blockers,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", required=True)
    parser.add_argument("--stack-name", required=True)
    parser.add_argument("--expected-handler", required=True)
    parser.add_argument("--expected-max-credits", required=True)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-attempts", type=int, default=4)
    args = parser.parse_args()

    try:
        result = run(
            region=args.region,
            stack_name=args.stack_name,
            expected_handler=args.expected_handler,
            expected_max_credits=args.expected_max_credits,
            template=args.template,
            max_attempts=args.max_attempts,
        )
    except Exception as exc:
        result = {
            "proofType": "MLB_HISTORICAL_NEXT_ROUND_CATCHUP",
            "version": VERSION,
            "createdAtUtc": datetime.now(timezone.utc).isoformat(),
            "ok": False,
            "error": f"{type(exc).__name__}:{exc}",
            "productionAuthorityChanged": False,
            "promotionGateChanged": False,
            "blockers": [str(exc)],
        }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
