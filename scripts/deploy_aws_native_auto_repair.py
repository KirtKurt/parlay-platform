#!/usr/bin/env python3
"""Deploy and accept all isolated AWS-native sports auto-repair control planes.

GitHub is used only as the tested-source delivery mechanism. After deployment,
EventBridge, Lambda, DynamoDB and CloudWatch perform recurring health checks and
safe operational recovery inside AWS.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

VERSION = "INQSI-AWS-NATIVE-AUTO-REPAIR-DEPLOY-v1"
TEMPLATE = Path(".aws-sam/build/template.yaml")
REPORT = Path("runtime_reports/aws_native_auto_repair_deployment_acceptance_latest.json")
STABLE_TARGET_STATUSES = {"CREATE_COMPLETE", "UPDATE_COMPLETE"}
UPDATEABLE_REPAIR_STATUSES = {
    "CREATE_COMPLETE",
    "UPDATE_COMPLETE",
    "UPDATE_ROLLBACK_COMPLETE",
}
ACCEPTED_RUNTIME_STATUSES = {"HEALTHY_OR_REPAIRED", "DEGRADED_FAIL_CLOSED"}
NON_AUTHORITY_FIELDS = (
    "production_authority_changed",
    "direct_sport_table_writes",
    "post_start_prediction_creation_allowed",
    "immutable_prediction_rewrite_allowed",
    "execution_lease_bypass_allowed",
    "gate_relaxation_allowed",
)


@dataclass(frozen=True)
class Profile:
    sport: str
    repair_stack: str
    target_stack: str
    function_prefix: str
    rule_prefix: str


PROFILES = (
    Profile(
        "mlb-core",
        "parlay-platform-auto-repair-mlb-core",
        "parlay-platform-dev",
        "parlay-platform-dev-",
        "parlay-platform-dev-",
    ),
    Profile(
        "mlb-auto",
        "parlay-platform-auto-repair-mlb-auto",
        "parlay-platform-mlb-auto-llm",
        "parlay-platform-mlb-auto-llm-",
        "parlay-platform-mlb-auto-llm-",
    ),
    Profile(
        "tennis",
        "parlay-platform-auto-repair-tennis",
        "parlay-platform-tennis-learning",
        "parlay-platform-tennis-learning-",
        "parlay-platform-tennis-learning-",
    ),
    Profile(
        "soccer",
        "parlay-platform-auto-repair-soccer",
        "parlay-platform-soccer-auto",
        "parlay-platform-soccer-auto-",
        "parlay-platform-soccer-auto-",
    ),
    Profile(
        "nfl",
        "parlay-platform-auto-repair-nfl",
        "parlay-platform-nfl-auto",
        "parlay-platform-nfl-auto-",
        "parlay-platform-nfl-auto-",
    ),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def command(args: Iterable[str]) -> None:
    values = [str(value) for value in args]
    print("+", " ".join(values), flush=True)
    subprocess.run(values, check=True)


def error_code(exc: ClientError) -> str:
    return str((exc.response.get("Error") or {}).get("Code") or "")


def describe_stack(cfn: Any, stack_name: str) -> dict[str, Any] | None:
    try:
        return (cfn.describe_stacks(StackName=stack_name).get("Stacks") or [None])[0]
    except ClientError as exc:
        if error_code(exc) == "ValidationError" and "does not exist" in str(exc):
            return None
        raise


def wait_for_repair_stack(cfn: Any, profile: Profile) -> None:
    for _ in range(60):
        stack = describe_stack(cfn, profile.repair_stack)
        if stack is None:
            return
        status = str(stack.get("StackStatus") or "UNKNOWN")
        if status in UPDATEABLE_REPAIR_STATUSES:
            return
        if status in {"ROLLBACK_COMPLETE", "ROLLBACK_FAILED"}:
            print(f"{profile.sport}: deleting failed repair stack {status}", flush=True)
            cfn.delete_stack(StackName=profile.repair_stack)
            cfn.get_waiter("stack_delete_complete").wait(
                StackName=profile.repair_stack,
                WaiterConfig={"Delay": 10, "MaxAttempts": 120},
            )
            return
        if status.endswith("_IN_PROGRESS"):
            time.sleep(15)
            continue
        raise RuntimeError(
            f"{profile.sport}: repair stack is not updateable: {status}"
        )
    raise TimeoutError(f"{profile.sport}: timed out waiting for repair stack")


def deploy(profile: Profile, region: str) -> None:
    command(
        (
            "sam",
            "deploy",
            "--template-file",
            str(TEMPLATE),
            "--stack-name",
            profile.repair_stack,
            "--region",
            region,
            "--resolve-s3",
            "--capabilities",
            "CAPABILITY_IAM",
            "--parameter-overrides",
            f"SportName={profile.sport}",
            f"TargetStackName={profile.target_stack}",
            f"FunctionNamePrefix={profile.function_prefix}",
            f"RuleNamePrefix={profile.rule_prefix}",
            "RepairLeaseSeconds=960",
            "--no-confirm-changeset",
            "--no-fail-on-empty-changeset",
        )
    )


def output_value(stack: dict[str, Any], key: str) -> str:
    for row in stack.get("Outputs") or []:
        if row.get("OutputKey") == key:
            return str(row.get("OutputValue") or "")
    return ""


def all_rules(events: Any, function_arn: str) -> list[str]:
    names: list[str] = []
    token: str | None = None
    while True:
        kwargs: dict[str, Any] = {"TargetArn": function_arn, "Limit": 100}
        if token:
            kwargs["NextToken"] = token
        page = events.list_rule_names_by_target(**kwargs)
        names.extend(str(value) for value in page.get("RuleNames") or [])
        token = page.get("NextToken")
        if not token:
            return sorted(set(names))


def invoke_cycle(lambda_client: Any, function_name: str) -> dict[str, Any]:
    response = lambda_client.invoke(
        FunctionName=function_name,
        InvocationType="RequestResponse",
        Payload=json.dumps(
            {"action": "cycle", "source": "deployment_acceptance"},
            separators=(",", ":"),
        ).encode("utf-8"),
    )
    raw = response["Payload"].read().decode("utf-8")
    payload = json.loads(raw or "{}")
    if response.get("FunctionError"):
        raise RuntimeError(
            "repair Lambda FunctionError: "
            + json.dumps(payload, sort_keys=True, default=str)[:3000]
        )
    if not isinstance(payload, dict):
        raise RuntimeError(f"repair Lambda returned non-object: {payload!r}")
    return payload


def accept(
    cfn: Any,
    lambda_client: Any,
    events: Any,
    profile: Profile,
) -> dict[str, Any]:
    stack = describe_stack(cfn, profile.repair_stack)
    if stack is None:
        raise RuntimeError(f"{profile.sport}: repair stack missing after deploy")
    stack_status = str(stack.get("StackStatus") or "UNKNOWN")
    if stack_status not in STABLE_TARGET_STATUSES:
        raise RuntimeError(
            f"{profile.sport}: repair stack not complete after deploy: {stack_status}"
        )

    function_name = output_value(stack, "AutoRepairFunctionName")
    state_table = output_value(stack, "AutoRepairStateTableName")
    effective_cadence = output_value(stack, "EffectiveRepairCadence")
    if not function_name or not state_table:
        raise RuntimeError(f"{profile.sport}: repair stack outputs are incomplete")
    if effective_cadence != "rate(5 minutes)":
        raise RuntimeError(
            f"{profile.sport}: repair cadence mismatch: {effective_cadence!r}"
        )

    configuration = lambda_client.get_function_configuration(
        FunctionName=function_name
    )
    if configuration.get("State") != "Active":
        raise RuntimeError(f"{profile.sport}: repair Lambda is not Active")
    if configuration.get("LastUpdateStatus") != "Successful":
        raise RuntimeError(
            f"{profile.sport}: repair Lambda update is not Successful"
        )
    function_arn = str(configuration.get("FunctionArn") or "")
    rules = all_rules(events, function_arn)
    if not rules:
        raise RuntimeError(
            f"{profile.sport}: no EventBridge rule targets the repair Lambda"
        )

    result = invoke_cycle(lambda_client, function_name)
    if result.get("version") != "INQSI-AWS-NATIVE-AUTO-REPAIR-v1":
        raise RuntimeError(f"{profile.sport}: runtime version mismatch: {result}")
    if result.get("sport") != profile.sport:
        raise RuntimeError(f"{profile.sport}: runtime sport mismatch: {result}")
    if result.get("target_stack") != profile.target_stack:
        raise RuntimeError(f"{profile.sport}: runtime target mismatch: {result}")
    if result.get("status") not in ACCEPTED_RUNTIME_STATUSES:
        raise RuntimeError(f"{profile.sport}: runtime status invalid: {result}")
    for field in NON_AUTHORITY_FIELDS:
        if result.get(field) is not False:
            raise RuntimeError(
                f"{profile.sport}: non-authority guard failed: {field}={result.get(field)!r}"
            )

    return {
        "profile": asdict(profile),
        "repair_stack_status": stack_status,
        "repair_function_name": function_name,
        "repair_state_table": state_table,
        "effective_repair_cadence": effective_cadence,
        "eventbridge_rules": rules,
        "first_cycle": result,
    }


def main() -> int:
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if not region:
        raise RuntimeError("AWS_REGION is required")
    if not TEMPLATE.is_file():
        raise RuntimeError(f"built SAM template is missing: {TEMPLATE}")

    normal_config = Config(retries={"mode": "adaptive", "max_attempts": 5})
    invoke_config = Config(
        connect_timeout=5,
        read_timeout=900,
        retries={"mode": "standard", "total_max_attempts": 1},
    )
    cfn = boto3.client("cloudformation", region_name=region, config=normal_config)
    events = boto3.client("events", region_name=region, config=normal_config)
    lambda_client = boto3.client(
        "lambda", region_name=region, config=invoke_config
    )
    sts = boto3.client("sts", region_name=region, config=normal_config)

    identity = sts.get_caller_identity()
    results: list[dict[str, Any]] = []
    for profile in PROFILES:
        print(f"=== {profile.sport}: target verification ===", flush=True)
        target = describe_stack(cfn, profile.target_stack)
        if target is None:
            raise RuntimeError(
                f"{profile.sport}: target stack does not exist: {profile.target_stack}"
            )
        target_status = str(target.get("StackStatus") or "UNKNOWN")
        if target_status not in STABLE_TARGET_STATUSES:
            raise RuntimeError(
                f"{profile.sport}: target stack is not stable: {target_status}"
            )
        wait_for_repair_stack(cfn, profile)
        deploy(profile, region)
        results.append(accept(cfn, lambda_client, events, profile))

    report = {
        "ok": True,
        "version": VERSION,
        "generated_at_utc": utc_now(),
        "git_sha": os.environ.get("GITHUB_SHA"),
        "region": region,
        "aws_account": identity.get("Account"),
        "caller_arn": identity.get("Arn"),
        "profiles": results,
        "all_control_planes_deployed": len(results) == len(PROFILES),
        "production_authority_changed": False,
        "direct_sport_table_writes": False,
        "post_start_prediction_creation_allowed": False,
        "immutable_prediction_rewrite_allowed": False,
        "execution_lease_bypass_allowed": False,
        "gate_relaxation_allowed": False,
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"AUTO_REPAIR_DEPLOYMENT_FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
