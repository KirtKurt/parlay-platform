#!/usr/bin/env python3
"""Deploy and prove the isolated MLB V8 shadow collector in AWS.

The recovery is fail-closed: it begins with a disabled EventBridge rule, verifies a
real bounded provider canary, enables the 15-minute rule, and only succeeds after
a later scheduled artifact appears in S3. V7 production authority is never changed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import boto3
from botocore.exceptions import ClientError

STACK_NAME = "parlay-platform-mlb-odds-v8-shadow"
PREFIX = "mlb/odds-v8-shadow/"
MAX_CREDITS = 100
MAX_EVENTS = 2


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None = None) -> str:
    return (value or utcnow()).isoformat()


def json_loads_bytes(value: Any) -> Any:
    if hasattr(value, "read"):
        value = value.read()
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    return json.loads(value)


def redact(text: str, secrets: Iterable[str]) -> str:
    result = str(text)
    for secret in secrets:
        if secret:
            result = result.replace(secret, "***")
    return result


def compact_error(exc: BaseException, secrets: Iterable[str]) -> str:
    return redact(f"{type(exc).__name__}: {exc}", secrets)[-4000:]


@dataclass
class RecoveryState:
    source_sha: str
    run_id: str
    run_attempt: str
    repository: str
    template_sha256: str
    started_at: str = field(default_factory=iso)
    step_outcomes: dict[str, str] = field(default_factory=dict)
    blockers: list[str] = field(default_factory=list)
    stack_recovery: dict[str, Any] = field(default_factory=lambda: {"statusHistory": [], "action": None})
    deployment_identity: dict[str, Any] | None = None
    manual_canary: dict[str, Any] | None = None
    scheduled_collection: dict[str, Any] | None = None
    scheduled_record: dict[str, Any] | None = None
    final_schedule_state: str = "UNKNOWN"
    cleanup: dict[str, Any] | None = None

    def mark(self, step: str, outcome: str) -> None:
        self.step_outcomes[step] = outcome

    def report(self) -> dict[str, Any]:
        return {
            "proofType": "MLB_ODDS_V8_SHADOW_RECOVERY",
            "createdAtUtc": iso(),
            "startedAtUtc": self.started_at,
            "sourceSha": self.source_sha,
            "runId": self.run_id,
            "runAttempt": self.run_attempt,
            "runUrl": f"https://github.com/{self.repository}/actions/runs/{self.run_id}" if self.repository and self.run_id else None,
            "stackName": STACK_NAME,
            "templateSha256": self.template_sha256,
            "authority": "SHADOW_ONLY",
            "productionAuthorityChanged": False,
            "finalScheduleState": self.final_schedule_state,
            "stepOutcomes": self.step_outcomes,
            "deploymentIdentity": self.deployment_identity,
            "manualCanary": self.manual_canary,
            "scheduledCollection": self.scheduled_collection,
            "scheduledRecord": self.scheduled_record,
            "stackRecovery": self.stack_recovery,
            "cleanup": self.cleanup,
            "blockers": self.blockers,
            "ok": not self.blockers and self.final_schedule_state == "ENABLED_EVERY_15_MINUTES",
        }


class V8Recovery:
    def __init__(
        self,
        *,
        region: str,
        template: Path,
        odds_api_key: str,
        state: RecoveryState,
        wait_seconds: int,
        poll_seconds: int,
    ) -> None:
        self.region = region
        self.template = template
        self.odds_api_key = odds_api_key
        self.state = state
        self.wait_seconds = wait_seconds
        self.poll_seconds = poll_seconds
        self.cf = boto3.client("cloudformation", region_name=region)
        self.lam = boto3.client("lambda", region_name=region)
        self.events = boto3.client("events", region_name=region)
        self.s3 = boto3.client("s3", region_name=region)
        self.schedule_enabled = False
        self.function_name = ""
        self.bucket_name = ""
        self.rule_name = ""

    @property
    def secrets(self) -> tuple[str, ...]:
        return (self.odds_api_key,)

    def _stack_status(self) -> str:
        try:
            rows = self.cf.describe_stacks(StackName=STACK_NAME).get("Stacks") or []
        except ClientError as exc:
            code = str((exc.response.get("Error") or {}).get("Code") or "")
            if code == "ValidationError" and "does not exist" in str(exc):
                return "STACK_MISSING"
            raise
        return str(rows[0].get("StackStatus") or "UNKNOWN") if rows else "STACK_MISSING"

    def _wait_stack(self, accepted: set[str], timeout_seconds: int = 1500) -> str:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            status = self._stack_status()
            self.state.stack_recovery["statusHistory"].append({"atUtc": iso(), "status": status})
            if status in accepted:
                return status
            if status.endswith("_IN_PROGRESS"):
                time.sleep(15)
                continue
            raise RuntimeError(f"CloudFormation entered non-updateable state {status}")
        raise TimeoutError("CloudFormation updateability wait timed out")

    def recover_stack(self) -> None:
        accepted = {
            "STACK_MISSING",
            "CREATE_COMPLETE",
            "UPDATE_COMPLETE",
            "UPDATE_ROLLBACK_COMPLETE",
            "IMPORT_COMPLETE",
            "IMPORT_ROLLBACK_COMPLETE",
        }
        status = self._stack_status()
        self.state.stack_recovery["statusHistory"].append({"atUtc": iso(), "status": status})
        if status in accepted:
            self.state.mark("recover", "success")
            return
        if status in {"ROLLBACK_COMPLETE", "CREATE_FAILED"}:
            events = self.cf.describe_stack_events(StackName=STACK_NAME).get("StackEvents") or []
            self.state.stack_recovery["failedCreateEvents"] = [
                {
                    "timestamp": row.get("Timestamp").isoformat() if row.get("Timestamp") else None,
                    "logicalResourceId": row.get("LogicalResourceId"),
                    "resourceType": row.get("ResourceType"),
                    "resourceStatus": row.get("ResourceStatus"),
                    "resourceStatusReason": redact(str(row.get("ResourceStatusReason") or ""), self.secrets),
                }
                for row in events[:50]
            ]
            self.cf.delete_stack(StackName=STACK_NAME)
            self.cf.get_waiter("stack_delete_complete").wait(
                StackName=STACK_NAME,
                WaiterConfig={"Delay": 10, "MaxAttempts": 120},
            )
            self.state.stack_recovery["action"] = "DELETED_FAILED_CREATE_STACK"
            self.state.mark("recover", "success")
            return
        if status == "UPDATE_ROLLBACK_FAILED":
            self.cf.continue_update_rollback(StackName=STACK_NAME)
            self.state.stack_recovery["action"] = "CONTINUED_UPDATE_ROLLBACK"
            self._wait_stack({"UPDATE_ROLLBACK_COMPLETE"})
            self.state.mark("recover", "success")
            return
        if status.endswith("_IN_PROGRESS"):
            self._wait_stack(accepted)
            self.state.mark("recover", "success")
            return
        raise RuntimeError(f"CloudFormation stack is not recoverable automatically: {status}")

    def _deploy(self, enabled: bool) -> None:
        cmd = [
            "sam",
            "deploy",
            "--template-file",
            str(self.template),
            "--stack-name",
            STACK_NAME,
            "--region",
            self.region,
            "--capabilities",
            "CAPABILITY_IAM",
            "--resolve-s3",
            "--force-upload",
            "--no-confirm-changeset",
            "--no-fail-on-empty-changeset",
            "--parameter-overrides",
            f"OddsApiKey={self.odds_api_key}",
            f"DeployGitSha={self.state.source_sha}",
            "V8Enabled=true",
            f"V8ScheduleEnabled={'true' if enabled else 'false'}",
            "V8FeaturedRegions=us",
            "V8EventRegions=us,us2",
            f"V8MaxEventsPerCycle={MAX_EVENTS}",
            f"V8MaxCreditsPerCycle={MAX_CREDITS}",
        ]
        result = subprocess.run(cmd, text=True, capture_output=True, check=False)
        output = redact((result.stdout or "") + "\n" + (result.stderr or ""), self.secrets)
        print(output[-12000:])
        if result.returncode != 0:
            raise RuntimeError(f"sam deploy failed with exit code {result.returncode}: {output[-4000:]}")

    def _outputs(self) -> dict[str, str]:
        stack = (self.cf.describe_stacks(StackName=STACK_NAME).get("Stacks") or [])[0]
        return {str(row.get("OutputKey")): str(row.get("OutputValue")) for row in stack.get("Outputs") or []}

    def deploy_canary(self) -> None:
        self._deploy(False)
        self.state.mark("canaryDeploy", "success")

    def verify_identity(self) -> None:
        outputs = self._outputs()
        self.function_name = outputs.get("ShadowCollectorFunctionName", "")
        self.bucket_name = outputs.get("ShadowArtifactsBucketName", "")
        if not self.function_name or not self.bucket_name:
            raise AssertionError(f"Missing required stack outputs: {outputs}")
        if outputs.get("Authority") != "SHADOW_ONLY":
            raise AssertionError(outputs)
        if outputs.get("ScheduleStatus") != "DISABLED_MANUAL_CANARY_ONLY":
            raise AssertionError(outputs)

        config = self.lam.get_function_configuration(FunctionName=self.function_name)
        concurrency = self.lam.get_function_concurrency(FunctionName=self.function_name)
        async_config = self.lam.get_function_event_invoke_config(FunctionName=self.function_name)
        function_arn = self.lam.get_function(FunctionName=self.function_name)["Configuration"]["FunctionArn"]
        rules = self.events.list_rule_names_by_target(TargetArn=function_arn).get("RuleNames") or []
        if len(rules) != 1:
            raise AssertionError(f"Expected one disabled EventBridge rule, found {rules}")
        self.rule_name = str(rules[0])
        rule = self.events.describe_rule(Name=self.rule_name)
        env = (config.get("Environment") or {}).get("Variables") or {}

        checks = {
            "handler": config.get("Handler") == "mlb_odds_v8_shadow_collector.lambda_handler",
            "deployGitSha": env.get("INQSI_DEPLOY_GIT_SHA") == self.state.source_sha,
            "enabled": env.get("MLB_V8_ENABLED") == "true",
            "playerPropsDisabled": env.get("MLB_V8_PLAYER_PROPS_ENABLED") == "false",
            "accountUnreserved": "ReservedConcurrentExecutions" not in concurrency,
            "asyncRetryDisabled": int(async_config.get("MaximumRetryAttempts", -1)) == 0,
            "asyncEventAgeBounded": int(async_config.get("MaximumEventAgeInSeconds", -1)) == 300,
            "ruleDisabled": rule.get("State") == "DISABLED",
        }
        if not all(checks.values()):
            raise AssertionError({"checks": checks, "config": config, "concurrency": concurrency, "async": async_config, "rule": rule})

        self.state.deployment_identity = {
            "functionName": self.function_name,
            "bucketName": self.bucket_name,
            "handler": config.get("Handler"),
            "deployGitSha": env.get("INQSI_DEPLOY_GIT_SHA"),
            "authority": outputs.get("Authority"),
            "scheduleStatusAtCanary": outputs.get("ScheduleStatus"),
            "scheduleRuleName": self.rule_name,
            "scheduleRuleStateAtCanary": rule.get("State"),
            "concurrencyMode": "ACCOUNT_UNRESERVED",
            "reservedConcurrency": None,
            "maximumRetryAttempts": async_config.get("MaximumRetryAttempts"),
            "maximumEventAgeInSeconds": async_config.get("MaximumEventAgeInSeconds"),
            "productionAuthorityChanged": False,
            "checks": checks,
        }
        self.state.mark("identity", "success")

    def _list_objects(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        paginator = self.s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket_name, Prefix=PREFIX):
            rows.extend(page.get("Contents") or [])
        return rows

    def invoke_canary(self) -> tuple[dict[str, Any], int, str]:
        response = self.lam.invoke(
            FunctionName=self.function_name,
            InvocationType="RequestResponse",
            Payload=json.dumps({"mode": "manual_recovery_proof"}).encode("utf-8"),
        )
        if response.get("FunctionError"):
            raise RuntimeError(f"Lambda canary FunctionError={response.get('FunctionError')}: {json_loads_bytes(response['Payload'])}")
        value = json_loads_bytes(response["Payload"])
        if not isinstance(value, Mapping):
            raise AssertionError(value)
        budget = value.get("budget") or {}
        artifact = value.get("artifact") or {}
        checks = {
            "ok": value.get("ok") is True,
            "status": value.get("status") == "COLLECTED_SHADOW",
            "authorityUnchanged": value.get("productionAuthorityChanged") is False,
            "withinBudget": budget.get("withinBudget") is True,
            "creditsBounded": int(budget.get("estimatedCredits") or 0) <= MAX_CREDITS,
            "artifactHash": bool(artifact.get("sha256")),
            "artifactKey": str(artifact.get("key") or "").endswith(".json"),
        }
        if not all(checks.values()):
            raise AssertionError({"checks": checks, "response": value})
        rows = self._list_objects()
        artifact_key = str(artifact["key"])
        if artifact_key not in {str(row.get("Key")) for row in rows}:
            raise AssertionError(f"Canary artifact was not observable in S3: {artifact_key}")
        self.state.manual_canary = dict(value)
        self.state.manual_canary["checks"] = checks
        self.state.mark("canary", "success")
        return dict(value), len(rows), artifact_key

    def enable_schedule(self) -> datetime:
        self._deploy(True)
        self.schedule_enabled = True
        enabled_at = utcnow()
        outputs = self._outputs()
        if outputs.get("ScheduleStatus") != "ENABLED_EVERY_15_MINUTES":
            raise AssertionError(outputs)
        rule = self.events.describe_rule(Name=self.rule_name)
        if rule.get("State") != "ENABLED":
            raise AssertionError(rule)
        self.state.mark("enable", "success")
        return enabled_at

    def prove_scheduled_collection(self, enabled_at: datetime, baseline_count: int, canary_key: str) -> None:
        deadline = time.monotonic() + self.wait_seconds
        candidate: dict[str, Any] | None = None
        observed: list[dict[str, Any]] = []
        while time.monotonic() < deadline:
            observed = self._list_objects()
            candidates = []
            for row in observed:
                key = str(row.get("Key") or "")
                modified = row.get("LastModified")
                if key and key != canary_key and isinstance(modified, datetime) and modified >= enabled_at:
                    candidates.append(row)
            if candidates:
                candidate = max(candidates, key=lambda row: row["LastModified"])
                break
            time.sleep(self.poll_seconds)
        if candidate is None:
            raise TimeoutError(f"No scheduled V8 S3 artifact appeared within {self.wait_seconds} seconds")

        scheduled_key = str(candidate["Key"])
        body = self.s3.get_object(Bucket=self.bucket_name, Key=scheduled_key)["Body"].read()
        record = json.loads(body.decode("utf-8"))
        collected_at = datetime.fromisoformat(str(record.get("collectedAtUtc") or "").replace("Z", "+00:00"))
        contract = record.get("contract") or {}
        budget = record.get("budget") or {}
        checks = {
            "collectedAfterEnable": collected_at >= enabled_at,
            "authorityUnchanged": record.get("productionAuthorityChanged") is False,
            "shadowOnly": contract.get("authority") == "SHADOW_ONLY",
            "v7Unchanged": contract.get("productionV7Unchanged") is True,
            "playerPropsDisabled": contract.get("playerPropsEnabled") is False,
            "withinBudget": budget.get("withinBudget") is True,
            "creditsBounded": int(budget.get("estimatedCredits") or 0) <= MAX_CREDITS,
            "objectCountAdvanced": len(observed) > baseline_count,
        }
        if not all(checks.values()):
            raise AssertionError({"checks": checks, "record": record})
        rule = self.events.describe_rule(Name=self.rule_name)
        if rule.get("State") != "ENABLED":
            raise AssertionError(rule)

        self.state.scheduled_collection = {
            "ruleName": self.rule_name,
            "ruleState": rule.get("State"),
            "scheduleStatus": "ENABLED_EVERY_15_MINUTES",
            "scheduleEnabledAtUtc": enabled_at.isoformat(),
            "scheduledArtifactKey": scheduled_key,
            "scheduledArtifactSha256": hashlib.sha256(body).hexdigest(),
            "scheduledCollectedAtUtc": record.get("collectedAtUtc"),
            "baselineObjectCount": baseline_count,
            "observedObjectCount": len(observed),
            "budget": budget,
            "contract": contract,
            "checks": checks,
            "productionAuthorityChanged": False,
        }
        self.state.scheduled_record = {
            "version": record.get("version"),
            "marketExpansionVersion": record.get("marketExpansionVersion"),
            "collectedAtUtc": record.get("collectedAtUtc"),
            "selectedEventCount": record.get("selectedEventCount"),
            "selectedMarketRequestCount": record.get("selectedMarketRequestCount"),
            "eventEnrichmentErrorCount": len(record.get("eventEnrichmentErrors") or []),
            "contract": contract,
            "budget": budget,
            "productionAuthorityChanged": record.get("productionAuthorityChanged"),
        }
        self.state.final_schedule_state = "ENABLED_EVERY_15_MINUTES"
        self.state.mark("scheduledVerification", "success")

    def disable_schedule(self) -> None:
        attempted_at = iso()
        try:
            self._deploy(False)
            outputs = self._outputs()
            state = self.events.describe_rule(Name=self.rule_name).get("State") if self.rule_name else None
            self.state.final_schedule_state = "DISABLED_AFTER_FAILED_VERIFICATION"
            self.state.cleanup = {
                "attemptedAtUtc": attempted_at,
                "ok": outputs.get("ScheduleStatus") == "DISABLED_MANUAL_CANARY_ONLY" and state == "DISABLED",
                "scheduleStatus": outputs.get("ScheduleStatus"),
                "ruleState": state,
            }
            self.state.mark("cleanup", "success" if self.state.cleanup["ok"] else "failure")
        except Exception as exc:
            self.state.cleanup = {"attemptedAtUtc": attempted_at, "ok": False, "error": compact_error(exc, self.secrets)}
            self.state.mark("cleanup", "failure")

    def run(self) -> None:
        self.recover_stack()
        self.deploy_canary()
        self.verify_identity()
        _, baseline_count, canary_key = self.invoke_canary()
        enabled_at = self.enable_schedule()
        self.prove_scheduled_collection(enabled_at, baseline_count, canary_key)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--region", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--wait-seconds", type=int, default=1500)
    parser.add_argument("--poll-seconds", type=int, default=30)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    odds_api_key = os.environ.get("ODDS_API_KEY", "").strip()
    if not odds_api_key:
        raise SystemExit("ODDS_API_KEY is required")
    if not args.template.is_file():
        raise SystemExit(f"Built SAM template is missing: {args.template}")
    template_sha = hashlib.sha256(args.template.read_bytes()).hexdigest()
    state = RecoveryState(
        source_sha=args.source_sha,
        run_id=os.environ.get("GITHUB_RUN_ID", ""),
        run_attempt=os.environ.get("GITHUB_RUN_ATTEMPT", ""),
        repository=os.environ.get("GITHUB_REPOSITORY", "KirtKurt/parlay-platform"),
        template_sha256=template_sha,
    )
    recovery = V8Recovery(
        region=args.region,
        template=args.template,
        odds_api_key=odds_api_key,
        state=state,
        wait_seconds=max(60, min(args.wait_seconds, 2400)),
        poll_seconds=max(10, min(args.poll_seconds, 120)),
    )
    exit_code = 0
    try:
        recovery.run()
    except Exception as exc:
        exit_code = 1
        state.blockers.append(compact_error(exc, recovery.secrets))
        failed_step = next(
            (name for name in ("recover", "canaryDeploy", "identity", "canary", "enable", "scheduledVerification") if name not in state.step_outcomes),
            "unknown",
        )
        state.mark(failed_step, "failure")
        if recovery.schedule_enabled:
            recovery.disable_schedule()
        elif state.final_schedule_state == "UNKNOWN":
            state.final_schedule_state = "DISABLED_OR_NOT_ENABLED"
    finally:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        report = state.report()
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")
        print(json.dumps({"ok": report["ok"], "blockers": report["blockers"], "finalScheduleState": report["finalScheduleState"]}, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
