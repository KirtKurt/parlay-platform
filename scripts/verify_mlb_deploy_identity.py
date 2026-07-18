from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import boto3


FUNCTIONS = {
    "MLBAuditedPullFunction": "ingest",
    "MLBDailyPickLockFunction": "lock",
    "MLBProductionVerifierFunction": "verifier",
    "MLBV3ReadFunction": "read",
    "MLBResultsSchedulerFunction": "settlement",
}

EXPECTED_SCHEDULES = {
    "ingest": ["cron(0/15 * * * ? *)"],
    "lock": ["rate(1 minute)"],
    "verifier": ["rate(5 minutes)"],
    "settlement": ["rate(15 minutes)"],
}

LEGACY_TOKENS = (
    "MLBBASEPULL",
    "MLBT2",
    "MLBT3",
    "MLBT4",
    "MLBHOTKICKOFF",
    "MLBHOTPULLRECOVERY",
)


def _rule_names_for_target(events: Any, target_arn: str) -> List[str]:
    names: List[str] = []
    token = None
    while True:
        args: Dict[str, Any] = {"TargetArn": target_arn, "Limit": 100}
        if token:
            args["NextToken"] = token
        response = events.list_rule_names_by_target(**args)
        names.extend(str(name) for name in response.get("RuleNames") or [])
        token = response.get("NextToken")
        if not token:
            return sorted(set(names))


def _stack_rule_names(events: Any, stack_name: str) -> List[str]:
    names: List[str] = []
    token = None
    while True:
        args: Dict[str, Any] = {"NamePrefix": stack_name, "Limit": 100}
        if token:
            args["NextToken"] = token
        response = events.list_rules(**args)
        names.extend(str(rule.get("Name")) for rule in response.get("Rules") or [] if rule.get("Name"))
        token = response.get("NextToken")
        if not token:
            return sorted(set(names))


def verify(*, stack_name: str, region: str, expected_git_sha: str, expected_template_sha256: str) -> Dict[str, Any]:
    cloudformation = boto3.client("cloudformation", region_name=region)
    lambdas = boto3.client("lambda", region_name=region)
    events = boto3.client("events", region_name=region)

    blockers: List[str] = []
    function_proofs: Dict[str, Any] = {}
    function_arns: Dict[str, str] = {}

    for logical_id, role in FUNCTIONS.items():
        try:
            resource = cloudformation.describe_stack_resource(
                StackName=stack_name,
                LogicalResourceId=logical_id,
            )["StackResourceDetail"]
            physical_id = str(resource.get("PhysicalResourceId") or "")
            config = lambdas.get_function_configuration(FunctionName=physical_id)
        except Exception as exc:
            blockers.append(f"FUNCTION_RESOLUTION_FAILED:{logical_id}:{exc}")
            continue

        environment = (config.get("Environment") or {}).get("Variables") or {}
        actual_git_sha = str(environment.get("INQSI_DEPLOY_GIT_SHA") or "")
        actual_template_sha = str(environment.get("INQSI_DEPLOY_TEMPLATE_SHA256") or "")
        arn = str(config.get("FunctionArn") or "")
        function_arns[role] = arn
        if actual_git_sha != expected_git_sha:
            blockers.append(f"DEPLOY_GIT_SHA_MISMATCH:{logical_id}")
        if actual_template_sha != expected_template_sha256:
            blockers.append(f"DEPLOY_TEMPLATE_SHA_MISMATCH:{logical_id}")

        function_proofs[logical_id] = {
            "role": role,
            "physicalId": physical_id,
            "functionArn": arn,
            "handler": config.get("Handler"),
            "runtime": config.get("Runtime"),
            "lastModified": config.get("LastModified"),
            "codeSha256": config.get("CodeSha256"),
            "version": config.get("Version"),
            "deployGitSha": actual_git_sha or None,
            "deployTemplateSha256": actual_template_sha or None,
            "identityMatches": actual_git_sha == expected_git_sha and actual_template_sha == expected_template_sha256,
        }

    schedule_proofs: Dict[str, Any] = {}
    for role, expected in EXPECTED_SCHEDULES.items():
        arn = function_arns.get(role)
        if not arn:
            blockers.append(f"SCHEDULE_TARGET_FUNCTION_MISSING:{role}")
            continue
        rule_names = _rule_names_for_target(events, arn)
        enabled_rules: List[Dict[str, Any]] = []
        for rule_name in rule_names:
            rule = events.describe_rule(Name=rule_name)
            targets = events.list_targets_by_rule(Rule=rule_name).get("Targets") or []
            if rule.get("State") == "ENABLED" and any(str(target.get("Arn") or "") == arn for target in targets):
                enabled_rules.append({
                    "name": rule_name,
                    "state": rule.get("State"),
                    "schedule": rule.get("ScheduleExpression"),
                    "targetArns": sorted(str(target.get("Arn") or "") for target in targets),
                })
        schedules = sorted(str(rule.get("schedule") or "") for rule in enabled_rules)
        if schedules != sorted(expected):
            blockers.append(f"SCHEDULE_MISMATCH:{role}:expected={expected}:actual={schedules}")
        schedule_proofs[role] = {
            "functionArn": arn,
            "enabledRules": enabled_rules,
            "expectedSchedules": expected,
            "exactMatch": schedules == sorted(expected),
        }

    legacy_rules: List[Dict[str, Any]] = []
    for rule_name in _stack_rule_names(events, stack_name):
        upper = rule_name.upper()
        if not any(token in upper for token in LEGACY_TOKENS):
            continue
        rule = events.describe_rule(Name=rule_name)
        if rule.get("State") == "ENABLED":
            legacy_rules.append({
                "name": rule_name,
                "state": rule.get("State"),
                "schedule": rule.get("ScheduleExpression"),
            })
    if legacy_rules:
        blockers.append("ENABLED_LEGACY_MLB_EVENTBRIDGE_RULES")

    return {
        "ok": not blockers,
        "proofType": "INQSI_MLB_DEPLOY_IDENTITY_AND_SCHEDULE_PROOF",
        "createdAtUtc": datetime.now(timezone.utc).isoformat(),
        "stackName": stack_name,
        "region": region,
        "expectedGitSha": expected_git_sha,
        "expectedTemplateSha256": expected_template_sha256,
        "functions": function_proofs,
        "schedules": schedule_proofs,
        "enabledLegacyRules": legacy_rules,
        "blockers": blockers,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stack-name", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--expected-git-sha", required=True)
    parser.add_argument("--expected-template-sha256", required=True)
    parser.add_argument("--output", default="runtime_reports/mlb_deploy_identity_latest.json")
    args = parser.parse_args()

    result = verify(
        stack_name=args.stack_name,
        region=args.region,
        expected_git_sha=args.expected_git_sha,
        expected_template_sha256=args.expected_template_sha256,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, default=str))
    if result.get("ok") is not True:
        raise SystemExit("MLB deployment identity verification failed: " + json.dumps(result.get("blockers")))


if __name__ == "__main__":
    main()
