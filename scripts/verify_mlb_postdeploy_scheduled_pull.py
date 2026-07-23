#!/usr/bin/env python3
"""Verify one normal scheduled MLB pull after an exact production deployment.

The verifier is deliberately read-only. It never invokes the pull Lambda. It
waits for the next EventBridge-owned canonical quarter-hour pull, verifies the
immutable provider manifest, observes the matching Lambda completion in
CloudWatch, and reconciles the public lock/prediction lifecycle.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import boto3
from boto3.dynamodb.conditions import Key

try:
    from scripts.mlb_deploy_http_probe import fetch_json_object
    from scripts.mlb_deploy_cutoff_smoke_policy import (
        ALLOWED_POST_CUTOFF_STATUSES,
        historical_lifecycle_acceptance,
    )
except ImportError:  # pragma: no cover - direct script execution
    from mlb_deploy_http_probe import fetch_json_object
    from mlb_deploy_cutoff_smoke_policy import (
        ALLOWED_POST_CUTOFF_STATUSES,
        historical_lifecycle_acceptance,
    )

VERSION = "MLB-POSTDEPLOY-SCHEDULED-PULL-OBSERVER-v1-no-manual-pull"
ET = ZoneInfo("America/New_York")
REPORT_RE = re.compile(r"^REPORT RequestId:\s*([A-Za-z0-9-]+)")
START_RE = re.compile(r"^START RequestId:\s*([A-Za-z0-9-]+)")
FAILURE_TOKENS = (
    "MLB_SCHEDULED_PULL_FAILED",
    "MLB_SCHEDULED_PULL_PREREQUISITE_FAILED",
    "Task timed out after",
    "Runtime.ExitError",
)


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def parse_utc(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def slot_time(item: Mapping[str, Any]) -> Optional[datetime]:
    parsed = parse_utc(item.get("slot_start_utc"))
    if parsed is not None:
        return parsed
    sk = str(item.get("SK") or "")
    marker = "PULL#SLOT#"
    return parse_utc(sk.split(marker, 1)[1]) if marker in sk else None


def row_identity(row: Mapping[str, Any]) -> str:
    return str(row.get("gameId") or row.get("gameIdentity") or "")


def row_status(row: Mapping[str, Any]) -> str:
    per_game = row.get("perGameCanonicalLock") or {}
    return str(
        row.get("lockStatus")
        or row.get("officialPredictionStatus")
        or (per_game.get("status") if isinstance(per_game, dict) else None)
        or ""
    ).strip().upper()


def select_fresh_pull(
    items: Iterable[Mapping[str, Any]],
    baseline_slot: Optional[datetime],
) -> Optional[Dict[str, Any]]:
    candidates: List[Tuple[datetime, Dict[str, Any]]] = []
    for raw in items:
        if raw.get("record_type") != "pull_run":
            continue
        slot = slot_time(raw)
        if slot is None:
            continue
        if baseline_slot is None or slot > baseline_slot:
            candidates.append((slot, dict(raw)))
    return min(candidates, key=lambda pair: pair[0])[1] if candidates else None


def matching_invocation_completion(
    events: Sequence[Mapping[str, Any]],
    observed_pull_at: datetime,
) -> Dict[str, Any]:
    by_stream: Dict[str, List[Mapping[str, Any]]] = {}
    for event in events:
        by_stream.setdefault(str(event.get("logStreamName") or ""), []).append(event)

    candidates: List[Dict[str, Any]] = []
    for stream_name, stream_events in by_stream.items():
        ordered = sorted(stream_events, key=lambda row: int(row.get("timestamp") or 0))
        starts: List[Tuple[datetime, str]] = []
        reports: List[Tuple[datetime, str, str]] = []
        failures: List[Tuple[datetime, str]] = []
        for event in ordered:
            message = str(event.get("message") or "")
            stamp = datetime.fromtimestamp(
                int(event.get("timestamp") or 0) / 1000,
                tz=timezone.utc,
            )
            start_match = START_RE.search(message)
            report_match = REPORT_RE.search(message)
            if start_match:
                starts.append((stamp, start_match.group(1)))
            if report_match:
                reports.append((stamp, report_match.group(1), message))
            if any(token in message for token in FAILURE_TOKENS):
                failures.append((stamp, message))

        eligible_starts = [
            row
            for row in starts
            if observed_pull_at - timedelta(seconds=90)
            <= row[0]
            <= observed_pull_at + timedelta(seconds=30)
        ]
        if not eligible_starts:
            continue
        request_ids = {request_id for _stamp, request_id in eligible_starts}
        completed = [row for row in reports if row[1] in request_ids]
        matching_failures = [
            row
            for row in failures
            if row[0] >= eligible_starts[0][0]
        ]
        candidates.append(
            {
                "logStreamName": stream_name,
                "startRequestIds": sorted(request_ids),
                "completedReports": completed,
                "failures": matching_failures,
            }
        )

    failures = [row for candidate in candidates for row in candidate["failures"]]
    if failures:
        return {
            "complete": False,
            "failed": True,
            "failureMessage": failures[-1][1],
            "candidates": candidates,
        }
    completed = [
        row
        for candidate in candidates
        for row in candidate["completedReports"]
    ]
    if not completed:
        return {"complete": False, "failed": False, "candidates": candidates}
    stamp, request_id, report = max(completed, key=lambda row: row[0])
    return {
        "complete": True,
        "failed": False,
        "requestId": request_id,
        "reportTimestampUtc": stamp.isoformat().replace("+00:00", "Z"),
        "report": report,
        "candidates": candidates,
    }


def classify_dispositions(
    status_rows: Sequence[Mapping[str, Any]],
    prediction_rows: Sequence[Mapping[str, Any]],
    *,
    now: datetime,
) -> Dict[str, Any]:
    status_by_id = {row_identity(row): dict(row) for row in status_rows}
    prediction_by_id = {row_identity(row): dict(row) for row in prediction_rows}
    errors: List[str] = []
    game_count = len(status_rows)
    if "" in status_by_id or len(status_by_id) != game_count:
        errors.append("status_identity_missing_or_duplicate")
    if "" in prediction_by_id or set(status_by_id) != set(prediction_by_id):
        errors.append("prediction_status_identity_mismatch")

    candidate_count = 0
    stored_candidate_count = 0
    canonical_locked_count = 0
    lifecycle_count = 0
    for game_id, status_entry in status_by_id.items():
        prediction_entry = prediction_by_id.get(game_id) or {}
        start = parse_utc(
            status_entry.get("commenceTime") or status_entry.get("commence_time")
        )
        if start is None:
            errors.append(f"{game_id}:commence_time_missing")
            continue
        cutoff = start - timedelta(minutes=45)
        winner = prediction_entry.get("predictedWinner")
        status_value = row_status(status_entry)
        if now < cutoff:
            candidate_count += 1
            if winner not in (None, ""):
                stored_candidate_count += 1
            else:
                errors.append(f"{game_id}:open_prelock_prediction_missing")
        elif status_entry.get("lockedPrediction") is True:
            canonical_locked_count += 1
            if winner in (None, ""):
                errors.append(f"{game_id}:canonical_locked_winner_missing")
        elif status_value in ALLOWED_POST_CUTOFF_STATUSES and winner in (None, ""):
            lifecycle_count += 1
        else:
            errors.append(f"{game_id}:invalid_postcutoff_disposition:{status_value}")

    disposition_count = candidate_count + canonical_locked_count + lifecycle_count
    if candidate_count != stored_candidate_count:
        errors.append("open_candidate_persistence_count_mismatch")
    if disposition_count != game_count:
        errors.append("storage_disposition_count_mismatch")
    return {
        "gameCount": game_count,
        "candidateCount": candidate_count,
        "storedCandidateCount": stored_candidate_count,
        "canonicalLockedCount": canonical_locked_count,
        "lifecycleCount": lifecycle_count,
        "dispositionCount": disposition_count,
        "complete": not errors,
        "errors": sorted(set(errors)),
    }


def _query_pull_items(table: Any, pk: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    start_key = None
    while True:
        kwargs: Dict[str, Any] = {
            "KeyConditionExpression": Key("PK").eq(pk),
            "ConsistentRead": True,
            "ScanIndexForward": True,
        }
        if start_key:
            kwargs["ExclusiveStartKey"] = start_key
        response = table.query(**kwargs)
        rows.extend(
            _plain(row)
            for row in (response.get("Items") or [])
            if isinstance(row, dict) and row.get("record_type") == "pull_run"
        )
        start_key = response.get("LastEvaluatedKey")
        if not start_key:
            return rows


def _log_events(
    logs: Any,
    log_group: str,
    start_at: datetime,
    end_at: datetime,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    token = None
    while True:
        kwargs: Dict[str, Any] = {
            "logGroupName": log_group,
            "startTime": int(start_at.timestamp() * 1000),
            "endTime": int(end_at.timestamp() * 1000),
            "interleaved": True,
            "limit": 10000,
        }
        if token:
            kwargs["nextToken"] = token
        response = logs.filter_log_events(**kwargs)
        rows.extend(response.get("events") or [])
        next_token = response.get("nextToken")
        if not next_token or next_token == token:
            return rows
        token = next_token


def observe(
    *,
    target_deploy_sha: str,
    region: str,
    stack_name: str,
    snapshots_table: str,
    max_wait_seconds: int,
    poll_seconds: int,
) -> Dict[str, Any]:
    import inqsi_pull_history as pull_history

    started_at = datetime.now(timezone.utc)
    cfn = boto3.client("cloudformation", region_name=region)
    stack = cfn.describe_stacks(StackName=stack_name)["Stacks"][0]
    outputs = {
        row.get("OutputKey"): row.get("OutputValue")
        for row in (stack.get("Outputs") or [])
    }
    api_url = str(outputs.get("ApiUrl") or "").rstrip("/")
    if not api_url:
        raise RuntimeError("live_api_url_missing")

    resource = cfn.describe_stack_resource(
        StackName=stack_name,
        LogicalResourceId="MLBAuditedPullFunction",
    )["StackResourceDetail"]
    function_name = resource["PhysicalResourceId"]
    lamb = boto3.client("lambda", region_name=region)
    logs = boto3.client("logs", region_name=region)
    config = lamb.get_function_configuration(FunctionName=function_name)
    timeout = int(config.get("Timeout") or 0)
    memory = int(config.get("MemorySize") or 0)
    deployed_sha = str(
        ((config.get("Environment") or {}).get("Variables") or {}).get(
            "INQSI_DEPLOY_GIT_SHA"
        )
        or ""
    )
    if timeout < 600:
        raise RuntimeError(f"live_timeout_below_600:{timeout}")
    if memory < 2048:
        raise RuntimeError(f"live_memory_below_2048:{memory}")
    if deployed_sha != target_deploy_sha:
        raise RuntimeError(
            f"deploy_identity_mismatch:live={deployed_sha}:expected={target_deploy_sha}"
        )

    slate_date = started_at.astimezone(ET).date().isoformat()
    table = boto3.resource("dynamodb", region_name=region).Table(snapshots_table)
    pull_pk = f"PULLS#mlb#{slate_date}"
    baseline = _query_pull_items(table, pull_pk)
    baseline_slots = [slot_time(row) for row in baseline]
    baseline_slots = [value for value in baseline_slots if value is not None]
    baseline_slot = max(baseline_slots) if baseline_slots else None

    deadline = time.monotonic() + max_wait_seconds
    observed_item: Optional[Dict[str, Any]] = None
    while time.monotonic() < deadline:
        observed_item = select_fresh_pull(_query_pull_items(table, pull_pk), baseline_slot)
        if observed_item is not None:
            break
        time.sleep(poll_seconds)
    if observed_item is None:
        raise RuntimeError("fresh_scheduled_canonical_pull_missing")

    observed_pull = observed_item.get("data") or {}
    observed_slot = slot_time(observed_item)
    observed_pull_at = parse_utc(observed_pull.get("pulled_at"))
    if not isinstance(observed_pull, dict) or observed_pull_at is None:
        raise RuntimeError("fresh_scheduled_pull_persisted_data_invalid")
    authority_errors = pull_history.validate_provider_schedule_manifest(
        observed_pull,
        slate_date,
        verify_immutable_storage=True,
    )
    if authority_errors:
        raise RuntimeError(
            "fresh_scheduled_pull_manifest_invalid:" + ",".join(authority_errors)
        )

    completion_deadline = time.monotonic() + min(max_wait_seconds, 12 * 60)
    completion: Dict[str, Any] = {}
    log_group = f"/aws/lambda/{function_name}"
    while time.monotonic() < completion_deadline:
        events = _log_events(
            logs,
            log_group,
            observed_pull_at - timedelta(seconds=90),
            datetime.now(timezone.utc) + timedelta(seconds=5),
        )
        completion = matching_invocation_completion(events, observed_pull_at)
        if completion.get("failed"):
            raise RuntimeError(
                "fresh_scheduled_pull_failed:"
                + str(completion.get("failureMessage") or "unknown")[:4000]
            )
        if completion.get("complete"):
            break
        time.sleep(poll_seconds)
    if not completion.get("complete"):
        raise RuntimeError("fresh_scheduled_pull_completion_report_missing")

    fetch_deadline = time.monotonic() + 8 * 60
    query = urllib.parse.urlencode({"date": slate_date})
    headers = {
        "accept": "application/json",
        "user-agent": "inqsi-postdeploy-scheduled-pull-observer/1.0",
    }
    status = fetch_json_object(
        api_url + "/v1/mlb/locks/status?" + query,
        deadline_monotonic=fetch_deadline,
        request_timeout_seconds=45,
        retry_delay_seconds=8,
        headers=headers,
    )
    predictions = fetch_json_object(
        api_url + "/v1/mlb/predictions?" + query,
        deadline_monotonic=fetch_deadline,
        request_timeout_seconds=45,
        retry_delay_seconds=8,
        headers=headers,
    )

    if status.get("ok") is not True or status.get("sport") != "mlb":
        raise RuntimeError("live_lock_status_unhealthy_after_scheduled_pull")
    if status.get("officialScheduleBacked") is not True:
        raise RuntimeError("live_lifecycle_not_official_schedule_backed")
    game_count = int(status.get("gameCount") or 0)
    status_rows = [
        row for row in (status.get("perGameStatus") or []) if isinstance(row, dict)
    ]
    if game_count <= 0 or len(status_rows) != game_count:
        raise RuntimeError("live_status_full_slate_coverage_missing")

    now = datetime.now(timezone.utc)
    historical_projection = historical_lifecycle_acceptance(
        predictions,
        status_rows,
        game_count,
        now=now,
    )
    prediction_rows = [
        row for row in (predictions.get("predictions") or []) if isinstance(row, dict)
    ]
    dispositions = classify_dispositions(status_rows, prediction_rows, now=now)
    if not dispositions["complete"]:
        raise RuntimeError(
            "fresh_scheduled_pull_lifecycle_disposition_failed:"
            + json.dumps(dispositions["errors"], sort_keys=True)
        )

    winner_summary = [
        {
            "gameDateEt": slate_date,
            "ok": True,
            "gameCount": game_count,
            "predictionCount": len(prediction_rows),
            "allGamesPredicted": all(
                row.get("predictedWinner") not in (None, "")
                for row in prediction_rows
            ),
            "displayStatusCoverageComplete": True,
            "lifecycleCoverageComplete": True,
            "preLockStorageLifecycleAware": True,
            "preLockStorageCandidateCount": dispositions["candidateCount"],
            "preLockStoredCount": dispositions["storedCandidateCount"],
            "preLockStorageComplete": (
                dispositions["candidateCount"]
                == dispositions["storedCandidateCount"]
            ),
            "preLockStorageLifecycleSkippedCount": dispositions["lifecycleCount"],
            "preLockStorageDispositionCount": dispositions["dispositionCount"],
            "preLockStorageDispositionComplete": dispositions["complete"],
            "canonicalLockedCount": dispositions["canonicalLockedCount"],
            "historicalStatusProjectionUsed": historical_projection,
            "operationalDefect": bool(
                status.get("operationalDefect")
                or predictions.get("operationalDefect")
            ),
        }
    ]
    return {
        "ok": True,
        "version": VERSION,
        "readOnly": True,
        "functionName": function_name,
        "timeoutSeconds": timeout,
        "memorySizeMb": memory,
        "deployedGitSha": deployed_sha,
        "targetDeploySha": target_deploy_sha,
        "functionLastModified": config.get("LastModified"),
        "verificationStartedAtUtc": started_at.isoformat(),
        "observedScheduledPullPk": observed_item.get("PK"),
        "observedScheduledPullSk": observed_item.get("SK"),
        "observedScheduledSlotUtc": observed_slot.isoformat() if observed_slot else None,
        "observedScheduledPullAtUtc": observed_pull_at.isoformat(),
        "scheduledRequestId": completion.get("requestId"),
        "scheduledReport": completion.get("report"),
        "manualPullInvoked": False,
        "statusCode": 200,
        "winnerResults": winner_summary,
        "secretExposed": False,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target-deploy-sha",
        default=os.environ.get("TARGET_DEPLOY_SHA") or "",
    )
    parser.add_argument("--region", default=os.environ.get("AWS_REGION") or "us-east-1")
    parser.add_argument("--stack-name", default="parlay-platform-dev")
    parser.add_argument(
        "--snapshots-table",
        default=os.environ.get("SNAPSHOTS_TABLE") or "parlay_platform_snapshots",
    )
    parser.add_argument("--max-wait-seconds", type=int, default=25 * 60)
    parser.add_argument("--poll-seconds", type=int, default=10)
    parser.add_argument(
        "--output",
        default="/tmp/mlb-post-deploy-invocation.json",
    )
    args = parser.parse_args(argv)
    if not args.target_deploy_sha:
        raise SystemExit("target deploy SHA is required")
    result = observe(
        target_deploy_sha=args.target_deploy_sha,
        region=args.region,
        stack_name=args.stack_name,
        snapshots_table=args.snapshots_table,
        max_wait_seconds=max(args.max_wait_seconds, 60),
        poll_seconds=max(args.poll_seconds, 1),
    )
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(result, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "ok": result.get("ok"),
                "manualPullInvoked": result.get("manualPullInvoked"),
                "observedScheduledPullSk": result.get("observedScheduledPullSk"),
                "scheduledRequestId": result.get("scheduledRequestId"),
                "winnerResults": result.get("winnerResults"),
                "output": str(path),
            },
            indent=2,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
