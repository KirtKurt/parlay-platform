#!/usr/bin/env python3
"""Reconcile every completed MLB slate pointer to the active V9 feature schema.

The runner invokes only the historical optimizer's zero-provider-call feature
rematerialization path, verifies every completed-slate pointer carries the same
feature dataset version, and stops before normal paid backfill resumes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

import boto3

VERSION = "MLB-V9-POINTER-RECONCILIATION-v1"
EXPECTED_DATASET = "MLB-HISTORICAL-FEATURE-DATASET-v9-v8-event-id-trainable"
EXPECTED_HANDLER = "mlb_historical_optimizer_v7_recovery_entrypoint.lambda_handler"


def _digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _outputs(cf: Any, stack_name: str) -> Dict[str, str]:
    stack = (cf.describe_stacks(StackName=stack_name).get("Stacks") or [])[0]
    return {
        str(row.get("OutputKey")): str(row.get("OutputValue"))
        for row in stack.get("Outputs") or []
        if row.get("OutputKey") and row.get("OutputValue")
    }


def _invoke(lam: Any, function_name: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
    response = lam.invoke(
        FunctionName=function_name,
        InvocationType="RequestResponse",
        Payload=json.dumps(dict(payload), separators=(",", ":")).encode("utf-8"),
    )
    body = response["Payload"].read().decode("utf-8")
    value = json.loads(body)
    if response.get("FunctionError"):
        raise RuntimeError(f"lambda_function_error:{response.get('FunctionError')}:{value}")
    if not isinstance(value, dict):
        raise RuntimeError("historical optimizer response is not an object")
    return value


def _completed(state: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    return [row for row in state.get("completedSlates") or [] if isinstance(row, Mapping)]


def pointer_version_counts(state: Mapping[str, Any]) -> Dict[str, int]:
    counter = Counter(str(row.get("featureDatasetVersion") or "MISSING") for row in _completed(state))
    return dict(sorted(counter.items()))


def first_mismatch(state: Mapping[str, Any]) -> Dict[str, Any] | None:
    for index, row in enumerate(_completed(state)):
        version = str(row.get("featureDatasetVersion") or "")
        if version != EXPECTED_DATASET:
            return {
                "index": index,
                "slateDateEt": row.get("slateDateEt"),
                "featureDatasetVersion": version or None,
                "artifact": row.get("artifact"),
            }
    return None


def integrity_checks(state: Mapping[str, Any]) -> Dict[str, bool]:
    completed = _completed(state)
    total = len(completed)
    rematerialized = int(state.get("featureRematerializedSlateCount") or 0)
    declared_total = int(state.get("featureRematerializationTotalSlateCount") or 0)
    return {
        "expectedDataset": state.get("featureDatasetVersion") == EXPECTED_DATASET,
        "completionTrue": state.get("featureRematerializationComplete") is True,
        "completedSlateCountPositive": total > 0,
        "stateCompleteSlateCountMatchesPointers": int(state.get("completeSlateCount") or 0) == total,
        "rematerializedCountMatchesPointers": rematerialized == total,
        "rematerializationTotalMatchesPointers": declared_total == total,
        "everyPointerVersionMatches": first_mismatch(state) is None,
        "rematerializationErrorsEmpty": not (state.get("featureRematerializationErrors") or []),
        "lastErrorEmpty": not state.get("lastError"),
        "paidRematerializationCallsZero": int(state.get("featureRematerializationPaidHistoricalCalls") or 0) == 0,
    }


def _summary(value: Mapping[str, Any]) -> Dict[str, Any]:
    state = value.get("state") or {}
    completed = _completed(state)
    return {
        "status": value.get("status") or state.get("phase"),
        "phase": state.get("phase"),
        "revision": state.get("revision"),
        "currentDate": state.get("currentDate"),
        "currentSlotIndex": state.get("currentSlotIndex"),
        "completeSlateCount": state.get("completeSlateCount"),
        "completedPointerCount": len(completed),
        "eligibleGameCount": state.get("eligibleGameCount"),
        "featureDatasetVersion": state.get("featureDatasetVersion"),
        "featureRematerializationComplete": state.get("featureRematerializationComplete"),
        "featureRematerializedSlateCount": state.get("featureRematerializedSlateCount"),
        "featureRematerializationTotalSlateCount": state.get("featureRematerializationTotalSlateCount"),
        "featureRematerializationTargetDatasetVersion": state.get("featureRematerializationTargetDatasetVersion"),
        "featureRematerializationVersion": state.get("featureRematerializationVersion"),
        "featureRematerializationPaidHistoricalCalls": state.get("featureRematerializationPaidHistoricalCalls"),
        "networkRequestCount": state.get("networkRequestCount"),
        "creditsConsumed": state.get("creditsConsumed"),
        "pointerVersionCounts": pointer_version_counts(state),
        "firstMismatch": first_mismatch(state),
        "checks": integrity_checks(state),
    }


def run(
    *,
    region: str,
    stack_name: str,
    output: Path,
    maximum_attempts: int = 90,
) -> Dict[str, Any]:
    if maximum_attempts < 1 or maximum_attempts > 200:
        raise ValueError("maximum attempts must be between 1 and 200")
    cf = boto3.client("cloudformation", region_name=region)
    lam = boto3.client("lambda", region_name=region)
    outputs = _outputs(cf, stack_name)
    function_name = outputs.get("HistoricalOptimizerFunctionName")
    if not function_name:
        raise RuntimeError("historical optimizer function output is missing")
    config = lam.get_function_configuration(FunctionName=function_name)
    environment = (config.get("Environment") or {}).get("Variables") or {}
    runtime_checks = {
        "handler": config.get("Handler") == EXPECTED_HANDLER,
        "datasetPatchSourceDeployed": bool(environment.get("INQSI_DEPLOY_GIT_SHA")),
        "endDate": environment.get("MLB_HISTORICAL_END_DATE") == "2026-07-24",
        "maximumRounds": int(environment.get("MLB_HISTORICAL_MAX_OPTIMIZATION_ROUNDS") or 0) >= 12,
    }
    if not all(runtime_checks.values()):
        raise RuntimeError("historical runtime identity failed:" + json.dumps(runtime_checks, sort_keys=True))

    baseline = _invoke(lam, function_name, {"mode": "status", "run": "v9_pointer_reconcile_baseline"})
    baseline_summary = _summary(baseline)
    baseline_authority = {
        "champion": baseline.get("champion"),
        "productionCutover": baseline.get("productionCutover"),
    }
    progress = []
    final = baseline
    for attempt in range(1, maximum_attempts + 1):
        checks = integrity_checks((final.get("state") or {}))
        progress.append(
            {
                "attempt": attempt,
                "atUtc": datetime.now(timezone.utc).isoformat(),
                "summary": _summary(final),
            }
        )
        if all(checks.values()):
            break
        result = _invoke(
            lam,
            function_name,
            {"mode": "orchestrate", "run": f"v9_pointer_reconcile_{os.environ.get('GITHUB_RUN_ID')}_{attempt}"},
        )
        if result.get("ok") is not True:
            raise RuntimeError(f"rematerialization_orchestration_failed:{result}")
        delay = 8 if result.get("status") == "LEASE_HELD" else 2
        time.sleep(delay)
        final = _invoke(
            lam,
            function_name,
            {"mode": "status", "run": f"v9_pointer_reconcile_status_{attempt}"},
        )
    else:
        raise TimeoutError("V9 pointer reconciliation did not complete within the attempt ceiling")

    final_summary = _summary(final)
    checks = integrity_checks(final.get("state") or {})
    if not all(checks.values()):
        raise RuntimeError("V9 pointer reconciliation remained incomplete:" + json.dumps(checks, sort_keys=True))
    final_authority = {
        "champion": final.get("champion"),
        "productionCutover": final.get("productionCutover"),
    }
    authority_unchanged = _digest(baseline_authority) == _digest(final_authority)
    report = {
        "proofType": "MLB_V9_POINTER_RECONCILIATION",
        "version": VERSION,
        "createdAtUtc": datetime.now(timezone.utc).isoformat(),
        "sourceSha": os.environ.get("GITHUB_SHA"),
        "runId": os.environ.get("GITHUB_RUN_ID"),
        "runUrl": (
            f"https://github.com/{os.environ.get('GITHUB_REPOSITORY')}/actions/runs/"
            f"{os.environ.get('GITHUB_RUN_ID')}"
            if os.environ.get("GITHUB_RUN_ID")
            else None
        ),
        "runtimeIdentity": {
            "stackName": stack_name,
            "functionName": function_name,
            "handler": config.get("Handler"),
            "deployGitSha": environment.get("INQSI_DEPLOY_GIT_SHA"),
            "checks": runtime_checks,
        },
        "baseline": baseline_summary,
        "final": final_summary,
        "progressTail": progress[-20:],
        "providerCallsMadeByReconciliation": 0,
        "backgroundNetworkRequestDelta": int(final_summary.get("networkRequestCount") or 0)
        - int(baseline_summary.get("networkRequestCount") or 0),
        "backgroundCreditDelta": int(final_summary.get("creditsConsumed") or 0)
        - int(baseline_summary.get("creditsConsumed") or 0),
        "authorityUnchanged": authority_unchanged,
        "productionAuthorityChanged": False,
        "checks": checks,
        "blockers": [] if authority_unchanged else ["authority_changed_during_pointer_reconciliation"],
        "ok": bool(authority_unchanged and all(checks.values())),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", required=True)
    parser.add_argument("--stack-name", default="parlay-platform-mlb-historical-optimizer")
    parser.add_argument("--output", required=True)
    parser.add_argument("--maximum-attempts", type=int, default=90)
    args = parser.parse_args()
    value = run(
        region=args.region,
        stack_name=args.stack_name,
        output=Path(args.output),
        maximum_attempts=args.maximum_attempts,
    )
    return 0 if value.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
