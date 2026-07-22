#!/usr/bin/env python3
"""Inspect the MLB pull Lambda runtime and recent logs without changing AWS.

The report is deliberately narrow and credential-safe. It records function
configuration, Lambda REPORT durations, timeout markers, and classified scorer
failures. Raw environment values and arbitrary application payloads are never
written.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import boto3

PROOF_TYPE = "MLB_PULL_LAMBDA_RUNTIME_READ_ONLY_DIAGNOSTIC"
VERSION = "MLB-PULL-LAMBDA-RUNTIME-DIAGNOSTIC-v1"
REPORT_RE = re.compile(
    r"REPORT RequestId:\s*(?P<request>[^\s]+).*?"
    r"Duration:\s*(?P<duration>[0-9.]+)\s*ms.*?"
    r"Billed Duration:\s*(?P<billed>[0-9]+)\s*ms.*?"
    r"Memory Size:\s*(?P<memory>[0-9]+)\s*MB.*?"
    r"Max Memory Used:\s*(?P<max_memory>[0-9]+)\s*MB"
)
TIMEOUT_RE = re.compile(r"Task timed out after\s*([0-9.]+)\s*seconds", re.I)
REQUEST_RE = re.compile(r"(?:RequestId:|requestId[:=])\s*([A-Za-z0-9-]+)", re.I)


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat()


def _query_events(logs: Any, log_group: str, start_ms: int, end_ms: int) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    token: Optional[str] = None
    while True:
        kwargs: Dict[str, Any] = {
            "logGroupName": log_group,
            "startTime": start_ms,
            "endTime": end_ms,
            "interleaved": True,
            "limit": 10000,
        }
        if token:
            kwargs["nextToken"] = token
        response = logs.filter_log_events(**kwargs)
        events.extend(response.get("events") or [])
        next_token = response.get("nextToken")
        if not next_token or next_token == token:
            return events
        token = next_token


def _classifications(message: str) -> List[str]:
    classes = []
    checks = {
        "SCHEDULED_PULL_FAILED": "MLB_SCHEDULED_PULL_FAILED",
        "SCHEDULED_RUNTIME_PREREQUISITE_FAILED": "MLB_SCHEDULED_PULL_PREREQUISITE_FAILED",
        "WINNER_ENGINE_UNAVAILABLE": "mlb_game_winner_engine_unavailable",
        "PRELOCK_STORAGE_INCOMPLETE": "prelock_storage_incomplete",
        "WINNER_COVERAGE_INCOMPLETE": "winner_prediction_coverage_incomplete",
        "WINNER_RESULT_MISSING": "winner_prediction_results_missing",
        "PROVIDER_MANIFEST_INCOMPLETE": "provider_schedule_manifest_incomplete",
        "LAMBDA_RUNTIME_EXIT_ERROR": "Runtime.ExitError",
        "PYTHON_TRACEBACK": "Traceback (most recent call last)",
    }
    for label, token in checks.items():
        if token in message:
            classes.append(label)
    if TIMEOUT_RE.search(message):
        classes.append("TASK_TIMEOUT")
    return classes


def build_report(
    *,
    region: str,
    stack_name: str,
    logical_id: str,
    hours: int,
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    cfn = boto3.client("cloudformation", region_name=region)
    lamb = boto3.client("lambda", region_name=region)
    logs = boto3.client("logs", region_name=region)

    resource = cfn.describe_stack_resource(
        StackName=stack_name,
        LogicalResourceId=logical_id,
    )["StackResourceDetail"]
    function_name = resource["PhysicalResourceId"]
    config = lamb.get_function_configuration(FunctionName=function_name)
    timeout_seconds = int(config.get("Timeout") or 0)
    log_group = f"/aws/lambda/{function_name}"
    start = now - timedelta(hours=max(hours, 1))
    events = _query_events(
        logs,
        log_group,
        int(start.timestamp() * 1000),
        int(now.timestamp() * 1000),
    )

    reports: List[Dict[str, Any]] = []
    timeouts: List[Dict[str, Any]] = []
    classified_errors: List[Dict[str, Any]] = []
    start_count = 0
    end_count = 0
    for event in events:
        message = str(event.get("message") or "")
        timestamp = int(event.get("timestamp") or 0)
        if message.startswith("START RequestId:"):
            start_count += 1
        if message.startswith("END RequestId:"):
            end_count += 1
        report_match = REPORT_RE.search(message)
        if report_match:
            row = {
                "timestampUtc": _iso(timestamp),
                "requestId": report_match.group("request"),
                "durationMs": float(report_match.group("duration")),
                "billedDurationMs": int(report_match.group("billed")),
                "memorySizeMb": int(report_match.group("memory")),
                "maxMemoryUsedMb": int(report_match.group("max_memory")),
            }
            reports.append(row)
        timeout_match = TIMEOUT_RE.search(message)
        if timeout_match:
            request_match = REQUEST_RE.search(message)
            timeouts.append({
                "timestampUtc": _iso(timestamp),
                "requestId": request_match.group(1) if request_match else None,
                "timeoutSecondsObserved": float(timeout_match.group(1)),
            })
        classes = _classifications(message)
        classes = [value for value in classes if value != "TASK_TIMEOUT"]
        if classes:
            request_match = REQUEST_RE.search(message)
            classified_errors.append({
                "timestampUtc": _iso(timestamp),
                "requestId": request_match.group(1) if request_match else None,
                "classifications": sorted(set(classes)),
            })

    reports.sort(key=lambda row: row["timestampUtc"])
    timeouts.sort(key=lambda row: row["timestampUtc"])
    classified_errors.sort(key=lambda row: row["timestampUtc"])
    max_duration_ms = max((row["durationMs"] for row in reports), default=None)
    near_timeout_count = sum(
        1
        for row in reports
        if timeout_seconds and row["durationMs"] >= timeout_seconds * 1000 * 0.90
    )
    if timeouts:
        diagnosis = "CONFIRMED_TASK_TIMEOUTS_IN_LOG_WINDOW"
    elif near_timeout_count:
        diagnosis = "NEAR_TIMEOUT_RUNTIME_PRESSURE"
    elif classified_errors:
        diagnosis = "CLASSIFIED_SCORING_FAILURE_WITHOUT_TIMEOUT_MARKER"
    else:
        diagnosis = "NO_TIMEOUT_OR_CLASSIFIED_FAILURE_FOUND_IN_LOG_WINDOW"

    environment_keys = sorted((config.get("Environment") or {}).get("Variables", {}).keys())
    return {
        "ok": True,
        "proofType": PROOF_TYPE,
        "version": VERSION,
        "createdAtUtc": now.isoformat().replace("+00:00", "Z"),
        "readOnly": True,
        "window": {
            "startUtc": start.isoformat(),
            "endUtc": now.isoformat(),
            "hours": hours,
        },
        "stack": {
            "stackName": stack_name,
            "logicalResourceId": logical_id,
            "functionName": function_name,
            "lastUpdatedTimestamp": str(resource.get("LastUpdatedTimestamp") or ""),
        },
        "functionConfiguration": {
            "runtime": config.get("Runtime"),
            "handler": config.get("Handler"),
            "timeoutSeconds": timeout_seconds,
            "memorySizeMb": config.get("MemorySize"),
            "lastModified": config.get("LastModified"),
            "codeSha256": config.get("CodeSha256"),
            "version": config.get("Version"),
            "environmentVariableNames": environment_keys,
            "environmentValuesExposed": False,
        },
        "logGroup": log_group,
        "summary": {
            "logEventCount": len(events),
            "startCount": start_count,
            "endCount": end_count,
            "reportCount": len(reports),
            "timeoutCount": len(timeouts),
            "nearTimeoutCount": near_timeout_count,
            "classifiedErrorCount": len(classified_errors),
            "maxDurationMs": max_duration_ms,
            "configuredTimeoutMs": timeout_seconds * 1000,
            "diagnosis": diagnosis,
        },
        "recentReports": reports[-30:],
        "timeouts": timeouts[-30:],
        "classifiedErrors": classified_errors[-50:],
        "secretExposed": False,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", default=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1")
    parser.add_argument("--stack-name", default="parlay-platform-dev")
    parser.add_argument("--logical-id", default="MLBAuditedPullFunction")
    parser.add_argument("--hours", type=int, default=12)
    parser.add_argument("--output", default="runtime_reports/mlb_pull_lambda_runtime_diagnostic_latest.json")
    args = parser.parse_args(argv)
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        report = build_report(
            region=args.region,
            stack_name=args.stack_name,
            logical_id=args.logical_id,
            hours=args.hours,
        )
    except Exception as exc:
        now = datetime.now(timezone.utc)
        report = {
            "ok": False,
            "proofType": PROOF_TYPE,
            "version": VERSION,
            "createdAtUtc": now.isoformat().replace("+00:00", "Z"),
            "readOnly": True,
            "error": f"{type(exc).__name__}: {exc}",
            "secretExposed": False,
        }
    path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": report.get("ok"),
        "functionName": (report.get("stack") or {}).get("functionName"),
        "summary": report.get("summary"),
        "output": str(path),
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
