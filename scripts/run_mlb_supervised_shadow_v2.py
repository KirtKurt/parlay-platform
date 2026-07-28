#!/usr/bin/env python3
"""Evaluate the supervised MLB challenger from immutable AWS historical data."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Mapping

import boto3

import mlb_supervised_daily_objective_v2_1 as daily_objective
import mlb_supervised_model_v2 as supervised

STATE_PK = "MLB_HISTORICAL_OPTIMIZER#V1"
STATE_SK = "STATE"
EXPECTED_HANDLER = "mlb_historical_optimizer_v7_recovery_entrypoint.lambda_handler"
MINIMUM_OPTIMIZATION_ROUNDS = 12
STALE_RANGE_ERROR = "configured historical range ended before the 1,000-train plus validation/audit evidence floor"

# Selection remains shadow-only and does not alter deployed authority.
daily_objective.install(supervised)


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def _sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _outputs(cf: Any, stack_name: str) -> Dict[str, str]:
    stacks = cf.describe_stacks(StackName=stack_name).get("Stacks") or []
    if not stacks:
        return {}
    return {
        str(row.get("OutputKey")): str(row.get("OutputValue"))
        for row in stacks[0].get("Outputs") or []
        if row.get("OutputKey") and row.get("OutputValue")
    }


def _resolve_function_name(cf: Any, lam: Any, stack_name: str) -> tuple[str, Dict[str, Any]]:
    attempts: List[Dict[str, Any]] = []
    outputs = _outputs(cf, stack_name)
    function_name = str(outputs.get("HistoricalOptimizerFunctionName") or "").strip()
    attempts.append({
        "strategy": "cloudformation_output",
        "found": bool(function_name),
        "outputKeys": sorted(outputs),
    })
    if function_name:
        return function_name, {"strategy": "cloudformation_output", "attempts": attempts}

    matches: List[str] = []
    paginator = lam.get_paginator("list_functions")
    for page in paginator.paginate():
        for row in page.get("Functions") or []:
            if row.get("Handler") == EXPECTED_HANDLER and row.get("FunctionName"):
                matches.append(str(row["FunctionName"]))
    matches = sorted(set(matches))
    attempts.append({"strategy": "lambda_handler_scan", "matches": matches})
    if len(matches) == 1:
        return matches[0], {"strategy": "lambda_handler_scan", "attempts": attempts}

    qualified: List[str] = []
    for name in matches:
        try:
            config = lam.get_function_configuration(FunctionName=name)
            env = ((config.get("Environment") or {}).get("Variables") or {})
            if (
                env.get("MLB_HISTORICAL_RANGE_EXTENSION_AUTHORIZED") == "true"
                and env.get("MLB_HISTORICAL_ARTIFACTS_BUCKET")
            ):
                qualified.append(name)
        except Exception as exc:
            attempts.append({
                "strategy": "lambda_environment_filter",
                "functionName": name,
                "error": f"{type(exc).__name__}:{exc}",
            })
    qualified = sorted(set(qualified))
    attempts.append({"strategy": "lambda_environment_filter", "matches": qualified})
    if len(qualified) == 1:
        return qualified[0], {"strategy": "lambda_environment_filter", "attempts": attempts}
    if not matches:
        raise RuntimeError(
            "historical optimizer Lambda could not be resolved:"
            + json.dumps(attempts, sort_keys=True)
        )
    raise RuntimeError(
        "historical optimizer Lambda resolution is ambiguous:"
        + json.dumps(attempts, sort_keys=True)
    )


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)) if value else None
    except Exception:
        return None


def _runtime_date_ready(*, configured_end: date | None, state_end: date | None, current_date: date | None) -> bool:
    """Validate coverage without requiring a future configured end to equal state."""
    if configured_end is None or state_end is None or current_date is None:
        return False
    return configured_end >= state_end and current_date >= state_end


def _feature_materialization_ready(state: Mapping[str, Any]) -> tuple[bool, Dict[str, Any]]:
    explicit = state.get("featureRematerializationComplete") is True
    completed = int(state.get("featureRematerializedSlateCount") or 0)
    total = int(state.get("featureRematerializationTotalSlateCount") or 0)
    errors = list(state.get("featureRematerializationErrors") or [])
    counts_complete = total > 0 and completed == total
    ready = (explicit or counts_complete) and not errors
    return ready, {
        "explicitComplete": explicit,
        "completedSlateCount": completed,
        "totalSlateCount": total,
        "countsComplete": counts_complete,
        "errorCount": len(errors),
    }


def _optimizer_error_status(
    state: Mapping[str, Any],
    *,
    configured_end: date | None,
    state_end: date | None,
    materialization_ready: bool,
) -> tuple[bool, Dict[str, Any]]:
    """Distinguish a stale pre-extension range error from an active blocker."""
    error = str(state.get("lastError") or "").strip()
    if not error:
        return True, {"blocking": False, "error": None, "classification": "NONE"}
    stale_range_error = bool(
        error == STALE_RANGE_ERROR
        and configured_end is not None
        and state_end is not None
        and configured_end > state_end
        and materialization_ready
        and int(state.get("completeSlateCount") or 0) > 0
        and int(state.get("eligibleGameCount") or 0) > 0
    )
    if stale_range_error:
        return True, {
            "blocking": False,
            "error": error,
            "classification": "STALE_PRE_EXTENSION_RANGE_EXHAUSTION",
        }
    return False, {"blocking": True, "error": error, "classification": "ACTIVE_OPTIMIZER_ERROR"}


def _load_records(state: Mapping[str, Any], s3: Any) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    completed_slates = state.get("completedSlates") or []
    if not completed_slates:
        raise RuntimeError("historical optimizer has no completed slate pointers")
    for slate in completed_slates:
        if not isinstance(slate, Mapping):
            continue
        pointer = slate.get("artifact") or {}
        bucket = str(pointer.get("bucket") or "")
        key = str(pointer.get("key") or "")
        if not bucket or not key:
            raise RuntimeError("completed slate artifact pointer is incomplete")
        response = s3.get_object(Bucket=bucket, Key=key)
        body = response["Body"].read()
        expected = str(pointer.get("sha256") or "")
        if expected and _sha(body) != expected:
            raise RuntimeError(f"completed slate checksum mismatch:{key}")
        dataset = json.loads(body.decode("utf-8"))
        if dataset.get("completeSlate") is not True:
            raise RuntimeError(f"completed slate lost complete flag:{key}")
        if dataset.get("postLockDataExcluded") is not True:
            raise RuntimeError(f"completed slate lost post-lock exclusion proof:{key}")
        if dataset.get("gameSpecificLockClipping") is not True:
            raise RuntimeError(f"completed slate lost per-game clipping proof:{key}")
        rows = dataset.get("records") or []
        if len(rows) != int(dataset.get("officialGameCount") or 0):
            raise RuntimeError(f"completed slate record count mismatch:{key}")
        records.extend(_plain(rows))
    if not records:
        raise RuntimeError("historical training corpus is empty")
    return records


def run(*, region: str, stack_name: str, table_name: str, output: Path) -> Dict[str, Any]:
    created = datetime.now(timezone.utc)
    cf = boto3.client("cloudformation", region_name=region)
    lam = boto3.client("lambda", region_name=region)
    ddb = boto3.resource("dynamodb", region_name=region)
    s3 = boto3.client("s3", region_name=region)

    function_name, resolution = _resolve_function_name(cf, lam, stack_name)
    config = lam.get_function_configuration(FunctionName=function_name)
    environment = (config.get("Environment") or {}).get("Variables") or {}

    item = ddb.Table(table_name).get_item(
        Key={"PK": STATE_PK, "SK": STATE_SK}, ConsistentRead=True
    ).get("Item")
    if not item:
        raise RuntimeError("historical optimizer state is missing")
    state = _plain(item.get("data") or {})

    configured_end = _parse_date(environment.get("MLB_HISTORICAL_END_DATE"))
    state_end = _parse_date(state.get("endDate"))
    current_date = _parse_date(state.get("currentDate"))
    materialization_ready, materialization_proof = _feature_materialization_ready(state)
    optimizer_error_free, optimizer_error_proof = _optimizer_error_status(
        state,
        configured_end=configured_end,
        state_end=state_end,
        materialization_ready=materialization_ready,
    )
    runtime_checks = {
        "handler": config.get("Handler") == EXPECTED_HANDLER,
        "rangeExtensionAuthorized": environment.get("MLB_HISTORICAL_RANGE_EXTENSION_AUTHORIZED") == "true",
        "maximumRoundsAtLeast12": int(environment.get("MLB_HISTORICAL_MAX_OPTIMIZATION_ROUNDS") or 0) >= MINIMUM_OPTIMIZATION_ROUNDS,
        "historicalEndDateConfigured": configured_end is not None,
        "historicalStateCoverageReady": _runtime_date_ready(
            configured_end=configured_end,
            state_end=state_end,
            current_date=current_date,
        ),
        "featureMaterializationReady": materialization_ready,
        "optimizerHasNoActiveBlockingError": optimizer_error_free,
    }
    if not all(runtime_checks.values()):
        raise RuntimeError(
            "canonical historical runtime readiness failed:"
            + json.dumps(
                {
                    "checks": runtime_checks,
                    "configuredEndDate": environment.get("MLB_HISTORICAL_END_DATE"),
                    "stateEndDate": state.get("endDate"),
                    "stateCurrentDate": state.get("currentDate"),
                    "featureMaterialization": materialization_proof,
                    "optimizerError": optimizer_error_proof,
                },
                sort_keys=True,
            )
        )

    records = _load_records(state, s3)
    result = supervised.train_and_evaluate(records)
    result.update({
        "proofType": "MLB_SUPERVISED_SHADOW_AWS_EVALUATION",
        "createdAtUtc": created.isoformat(),
        "sourceSha": os.environ.get("GITHUB_SHA"),
        "runId": os.environ.get("GITHUB_RUN_ID"),
        "runUrl": (
            f"https://github.com/{os.environ.get('GITHUB_REPOSITORY')}/actions/runs/"
            f"{os.environ.get('GITHUB_RUN_ID')}"
            if os.environ.get("GITHUB_RUN_ID") else None
        ),
        "runtimeIdentity": {
            "stackName": stack_name,
            "functionName": function_name,
            "functionResolution": resolution,
            "handler": config.get("Handler"),
            "deployGitSha": environment.get("INQSI_DEPLOY_GIT_SHA"),
            "configuredHistoricalEndDate": environment.get("MLB_HISTORICAL_END_DATE"),
            "checks": runtime_checks,
            "featureMaterialization": materialization_proof,
            "optimizerError": optimizer_error_proof,
        },
        "selectionObjective": dict(supervised.SUPERVISED_SELECTION_OBJECTIVE),
        "historicalState": {
            "phase": state.get("phase"),
            "optimizationRound": state.get("optimizationRound"),
            "currentDate": state.get("currentDate"),
            "endDate": state.get("endDate"),
            "currentSlotIndex": state.get("currentSlotIndex"),
            "networkRequestCount": state.get("networkRequestCount"),
            "eligibleGameCount": state.get("eligibleGameCount"),
            "completeSlateCount": state.get("completeSlateCount"),
            "featureDatasetVersion": state.get("featureDatasetVersion"),
            "featureRematerializedSlateCount": state.get("featureRematerializedSlateCount"),
            "featureRematerializationTotalSlateCount": state.get("featureRematerializationTotalSlateCount"),
            "quotaRemaining": (state.get("lastQuota") or {}).get("x-requests-remaining"),
        },
        "recordCountLoaded": len(records),
        "authority": "SHADOW_ONLY",
        "productionAuthorityChanged": False,
        "automaticWagerAllowed": False,
    })
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", required=True)
    parser.add_argument("--stack-name", default="parlay-platform-mlb-historical-optimizer")
    parser.add_argument("--table-name", default="parlay_platform_snapshots")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    value = run(
        region=args.region,
        stack_name=args.stack_name,
        table_name=args.table_name,
        output=Path(args.output),
    )
    print(json.dumps({
        "ok": value.get("ok"),
        "version": value.get("version"),
        "featureCoverage": value.get("featureCoverage"),
        "selectionObjective": value.get("selectionObjective"),
        "selectedFeatureGroup": (value.get("selection") or {}).get("selectedFeatureGroup"),
        "promotionGate": value.get("promotionGate"),
        "walkForward": ((value.get("metrics") or {}).get("walkForward")),
        "untouchedAudit": ((value.get("metrics") or {}).get("untouchedAudit")),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
