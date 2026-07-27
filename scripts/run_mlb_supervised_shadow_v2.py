#!/usr/bin/env python3
"""Evaluate the supervised MLB challenger from immutable AWS historical data."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Mapping

import boto3

import mlb_supervised_daily_objective_v2_1 as daily_objective
import mlb_supervised_model_v2 as supervised

STATE_PK = "MLB_HISTORICAL_OPTIMIZER#V1"
STATE_SK = "STATE"
EXPECTED_HANDLER = "mlb_historical_optimizer_v7_recovery_entrypoint.lambda_handler"
EXPECTED_HISTORICAL_END_DATE = "2026-07-26"
MINIMUM_OPTIMIZATION_ROUNDS = 12

# Selection must optimize the actual 80%-per-slate gate rather than log loss
# alone. The patch remains shadow-only and does not alter deployed authority.
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
    stack = (cf.describe_stacks(StackName=stack_name).get("Stacks") or [])[0]
    return {
        str(row.get("OutputKey")): str(row.get("OutputValue"))
        for row in stack.get("Outputs") or []
        if row.get("OutputKey") and row.get("OutputValue")
    }


def _load_records(state: Mapping[str, Any], s3: Any) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for slate in state.get("completedSlates") or []:
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
    return records


def run(*, region: str, stack_name: str, table_name: str, output: Path) -> Dict[str, Any]:
    created = datetime.now(timezone.utc)
    cf = boto3.client("cloudformation", region_name=region)
    lam = boto3.client("lambda", region_name=region)
    ddb = boto3.resource("dynamodb", region_name=region)
    s3 = boto3.client("s3", region_name=region)
    outputs = _outputs(cf, stack_name)
    function_name = outputs.get("HistoricalOptimizerFunctionName")
    if not function_name:
        raise RuntimeError("historical optimizer function output is missing")
    config = lam.get_function_configuration(FunctionName=function_name)
    environment = (config.get("Environment") or {}).get("Variables") or {}
    runtime_checks = {
        "handler": config.get("Handler") == EXPECTED_HANDLER,
        "rangeExtensionAuthorized": environment.get("MLB_HISTORICAL_RANGE_EXTENSION_AUTHORIZED") == "true",
        "maximumRoundsAtLeast12": int(environment.get("MLB_HISTORICAL_MAX_OPTIMIZATION_ROUNDS") or 0) >= MINIMUM_OPTIMIZATION_ROUNDS,
        "historicalEndDate": environment.get("MLB_HISTORICAL_END_DATE") == EXPECTED_HISTORICAL_END_DATE,
    }
    if not all(runtime_checks.values()):
        raise RuntimeError("canonical historical runtime identity failed:" + json.dumps(runtime_checks, sort_keys=True))
    item = ddb.Table(table_name).get_item(
        Key={"PK": STATE_PK, "SK": STATE_SK}, ConsistentRead=True
    ).get("Item")
    if not item:
        raise RuntimeError("historical optimizer state is missing")
    state = _plain(item.get("data") or {})
    if state.get("featureRematerializationComplete") is not True:
        raise RuntimeError("feature rematerialization is incomplete")
    if state.get("featureRematerializationErrors"):
        raise RuntimeError("feature rematerialization errors remain")
    if state.get("lastError"):
        raise RuntimeError("historical optimizer state has an unresolved error")
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
        "selectionObjective": dict(supervised.SUPERVISED_SELECTION_OBJECTIVE),
        "historicalState": {
            "phase": state.get("phase"),
            "optimizationRound": state.get("optimizationRound"),
            "currentDate": state.get("currentDate"),
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
