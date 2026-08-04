#!/usr/bin/env python3
"""Warm and verify the read-only MLB lock-status Lambda contract directly.

The public API smoke can be the first invocation of the large lock package after
SAM deployment. Direct invocation gives the lock Lambda its full timeout to
build and cache the authoritative status document, preserves the exact
application response for diagnostics, and prevents an API Gateway delivery
timeout from being mistaken for an application-contract failure.
"""
from __future__ import annotations

import argparse
import base64
import json
import time
from pathlib import Path
from typing import Any, Mapping

import boto3
from botocore.config import Config

EXPECTED_MODEL_VERSION = (
    "INQSI-MLB-DAILY-LOCK-v5-tminus45-readiness-release-status"
)
EXPECTED_SCHEDULE_VERSION = (
    "MLB-OFFICIAL-SCHEDULE-AUTHORITY-v1-statsapi-exact-date"
)
EXPECTED_FIX_VERSION = (
    "MLB-LOCK-RUNTIME-FIX-v5-official-schedule-lifecycle-vector-separation"
)


def _load_http_payload(raw: Any) -> tuple[int, dict[str, Any]]:
    if not isinstance(raw, Mapping):
        raise RuntimeError("lock Lambda returned a non-object response")
    status = int(raw.get("statusCode") or 0)
    body: Any = raw.get("body")
    if raw.get("isBase64Encoded") is True and isinstance(body, str):
        body = base64.b64decode(body).decode("utf-8")
    if isinstance(body, str):
        body = json.loads(body)
    if not isinstance(body, Mapping):
        raise RuntimeError("lock Lambda response body is not a JSON object")
    return status, dict(body)


def validate(payload: Mapping[str, Any]) -> None:
    required = (
        "ok",
        "sport",
        "slateDateEt",
        "modelVersion",
        "lockPolicy",
        "lockMinutesBeforeEachGame",
        "readinessCheckpointsMinutesBeforeGame",
        "playabilityCheckpointsMinutesBeforeGame",
        "gameCount",
        "officialScheduleBacked",
        "officialScheduleAuthorityVersion",
        "officialScheduleGameCount",
        "officialScheduleAuthoritativeStartTimes",
        "lockedPredictionCount",
        "lockedStatusCount",
        "noPredictionDataCount",
        "lockStatusComplete",
        "canonicalPredictionComplete",
        "operationalDefect",
        "perGameLockInstallation",
        "mlLockVectorPreservation",
        "perGameStatus",
    )
    missing = [name for name in required if name not in payload]
    if missing:
        raise RuntimeError(f"lock status missing fields:{missing}")
    if payload.get("ok") is not True or payload.get("sport") != "mlb":
        raise RuntimeError("lock status is unhealthy")
    if payload.get("modelVersion") != EXPECTED_MODEL_VERSION:
        raise RuntimeError("lock status model version is stale")
    if payload.get("lockMinutesBeforeEachGame") != 45:
        raise RuntimeError("lock status lost T-minus-45")
    if payload.get("readinessCheckpointsMinutesBeforeGame") != [60, 50]:
        raise RuntimeError("lock readiness checkpoints are stale")
    if payload.get("playabilityCheckpointsMinutesBeforeGame") != [30, 15]:
        raise RuntimeError("lock playability checkpoints are stale")
    if payload.get("officialScheduleBacked") is not True:
        raise RuntimeError("lock status is not official-schedule-backed")
    if payload.get("officialScheduleAuthorityVersion") != EXPECTED_SCHEDULE_VERSION:
        raise RuntimeError("lock status schedule authority is stale")
    if payload.get("officialScheduleAuthoritativeStartTimes") is not True:
        raise RuntimeError("lock status start-time authority is not official")

    rows = payload.get("perGameStatus")
    if not isinstance(rows, list):
        raise RuntimeError("perGameStatus is not a list")
    game_count = int(payload.get("gameCount") or 0)
    if game_count <= 0 or len(rows) != game_count:
        raise RuntimeError("lock status does not cover the full scheduled slate")
    if int(payload.get("officialScheduleGameCount") or 0) != game_count:
        raise RuntimeError("official schedule count differs from game count")

    locked_predictions = int(payload.get("lockedPredictionCount") or 0)
    locked_statuses = int(payload.get("lockedStatusCount") or 0)
    terminal_no_data = int(payload.get("noPredictionDataCount") or 0)
    if locked_statuses != locked_predictions + terminal_no_data:
        raise RuntimeError("lock status conflates predictions and terminal outcomes")
    if not (0 <= locked_predictions <= locked_statuses <= game_count):
        raise RuntimeError("lock status counts exceed the scheduled slate")

    installation = payload.get("perGameLockInstallation") or {}
    if installation.get("ok") is not True:
        raise RuntimeError("per-game lock installation is unhealthy")
    if installation.get("fixVersion") != EXPECTED_FIX_VERSION:
        raise RuntimeError("per-game lock runtime fix is stale")
    if installation.get("officialScheduleAuthorityRequired") is not True:
        raise RuntimeError("per-game lock does not require official schedule authority")
    if installation.get("selectionLockIndependentOfTrainingVector") is not True:
        raise RuntimeError("selection lock still depends on training eligibility")

    vector = payload.get("mlLockVectorPreservation") or {}
    if vector.get("selectionLockIndependentOfTrainingVector") is not True:
        raise RuntimeError("lock vector separation is stale")


def invoke(
    *,
    function_name: str,
    region: str,
    attempts: int,
    delay_seconds: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    client = boto3.client(
        "lambda",
        region_name=region,
        config=Config(
            connect_timeout=10,
            read_timeout=300,
            retries={"max_attempts": 2, "mode": "standard"},
        ),
    )
    event = {
        "version": "2.0",
        "routeKey": "GET /v1/mlb/locks/status",
        "rawPath": "/v1/mlb/locks/status",
        "path": "/v1/mlb/locks/status",
        "httpMethod": "GET",
        "headers": {"accept": "application/json"},
        "queryStringParameters": None,
        "requestContext": {
            "http": {
                "method": "GET",
                "path": "/v1/mlb/locks/status",
            }
        },
        "run": "locks_status_route",
    }
    last_error: Exception | None = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            response = client.invoke(
                FunctionName=function_name,
                InvocationType="RequestResponse",
                Payload=json.dumps(event, separators=(",", ":")).encode("utf-8"),
            )
            stream = response.get("Payload")
            encoded = stream.read() if stream is not None else b""
            raw = json.loads(encoded.decode("utf-8")) if encoded else {}
            if response.get("FunctionError"):
                raise RuntimeError(
                    "lock Lambda FunctionError:"
                    + json.dumps(raw, sort_keys=True, default=str)
                )
            status, payload = _load_http_payload(raw)
            if status != 200:
                raise RuntimeError(
                    f"lock Lambda status={status}:"
                    + json.dumps(payload, sort_keys=True, default=str)
                )
            validate(payload)
            invocation = {
                "functionName": function_name,
                "region": region,
                "attempt": attempt,
                "statusCode": status,
                "executedVersion": response.get("ExecutedVersion"),
                "requestId": (
                    (response.get("ResponseMetadata") or {}).get("RequestId")
                ),
                "gameCount": int(payload.get("gameCount") or 0),
                "slateDateEt": payload.get("slateDateEt"),
            }
            return payload, invocation
        except Exception as exc:
            last_error = exc
            if attempt >= max(1, attempts):
                break
            time.sleep(max(0.0, delay_seconds) * attempt)
    raise RuntimeError(f"direct lock-status verification failed:{last_error}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--function-name", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--invocation-output", required=True, type=Path)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--delay-seconds", type=float, default=4.0)
    args = parser.parse_args()

    payload, invocation = invoke(
        function_name=args.function_name,
        region=args.region,
        attempts=args.attempts,
        delay_seconds=args.delay_seconds,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.invocation_output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    args.invocation_output.write_text(
        json.dumps(invocation, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(invocation, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
