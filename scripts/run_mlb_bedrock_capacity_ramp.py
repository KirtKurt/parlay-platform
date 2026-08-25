from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

import boto3
from botocore.config import Config


EXPECTED_HANDLER = "capacity_manager.lambda_handler"
DEFAULT_LOGICAL_ID = "MLBAutoBedrockCapacityManagerFunction"

_CLIENT_CONFIG = Config(
    connect_timeout=15,
    read_timeout=900,
    retries={"mode": "standard", "total_max_attempts": 4},
)


def _json_object(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _read_payload(stream: Any) -> Dict[str, Any]:
    raw = stream.read() if hasattr(stream, "read") else stream
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    parsed = json.loads(str(raw or "{}"))
    return _json_object(parsed)


def wait_for_capacity_function(
    *,
    cloudformation: Any,
    lambda_client: Any,
    stack_name: str,
    logical_id: str,
    deadline_seconds: int,
    poll_seconds: int,
) -> str:
    deadline = time.monotonic() + max(1, int(deadline_seconds))
    last_error = "capacity manager not found"
    while time.monotonic() < deadline:
        try:
            detail = cloudformation.describe_stack_resource(
                StackName=stack_name,
                LogicalResourceId=logical_id,
            ).get("StackResourceDetail") or {}
            function_name = str(detail.get("PhysicalResourceId") or "").strip()
            if function_name:
                config = lambda_client.get_function_configuration(
                    FunctionName=function_name
                )
                handler = str(config.get("Handler") or "")
                status = str(config.get("LastUpdateStatus") or "Successful")
                state = str(config.get("State") or "Active")
                if (
                    handler == EXPECTED_HANDLER
                    and status == "Successful"
                    and state == "Active"
                ):
                    return function_name
                last_error = (
                    f"function not ready: handler={handler}, "
                    f"lastUpdateStatus={status}, state={state}"
                )
        except Exception as exc:
            last_error = f"{type(exc).__name__}:{exc}"
        time.sleep(max(1, int(poll_seconds)))
    raise TimeoutError(last_error)


def invoke_capacity_manager(
    *,
    lambda_client: Any,
    function_name: str,
    attempt: int,
    desired_multiplier: float,
    max_quota_requests: int,
    first_max_routes: int,
    retry_max_routes: int,
) -> Dict[str, Any]:
    payload = {
        "requestQuotaIncreases": True,
        "desiredMultiplier": float(desired_multiplier),
        "maxQuotaRequests": int(max_quota_requests),
        "maxRoutesPerRegion": int(
            first_max_routes if attempt == 1 else retry_max_routes
        ),
    }
    try:
        response = lambda_client.invoke(
            FunctionName=function_name,
            InvocationType="RequestResponse",
            Payload=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        )
        result = _read_payload(response.get("Payload"))
        result.update(
            {
                "capacityWorkflowAttempt": attempt,
                "lambdaFunctionError": response.get("FunctionError"),
                "lambdaStatusCode": response.get("StatusCode"),
                "lambdaExecutedVersion": response.get("ExecutedVersion"),
            }
        )
        return result
    except Exception as exc:
        response = getattr(exc, "response", {}) or {}
        metadata = response.get("ResponseMetadata") or {}
        return {
            "ok": False,
            "liveCapacityOk": False,
            "capacityWorkflowAttempt": attempt,
            "lambdaInvocationError": {
                "type": type(exc).__name__,
                "code": str(
                    (response.get("Error") or {}).get("Code")
                    or type(exc).__name__
                ),
                "message": str(exc)[:700],
                "requestId": metadata.get("RequestId"),
                "httpStatusCode": metadata.get("HTTPStatusCode"),
            },
        }


def _annotate(report: Dict[str, Any]) -> Dict[str, Any]:
    value = dict(report)
    value.update(
        {
            "sourceSha": os.environ.get("GITHUB_SHA"),
            "runId": os.environ.get("GITHUB_RUN_ID"),
            "runAttempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        }
    )
    return value


def _capacity_live(report: Mapping[str, Any]) -> bool:
    return bool(
        report.get("liveCapacityOk") is True
        and report.get("capacityManagerRoleUsed") is True
        and report.get("lambdaFunctionError") in (None, "")
        and not report.get("lambdaInvocationError")
    )


def run(args: argparse.Namespace) -> int:
    cloudformation = boto3.client(
        "cloudformation", region_name=args.region, config=_CLIENT_CONFIG
    )
    lambda_client = boto3.client(
        "lambda", region_name=args.region, config=_CLIENT_CONFIG
    )
    function_name = wait_for_capacity_function(
        cloudformation=cloudformation,
        lambda_client=lambda_client,
        stack_name=args.stack_name,
        logical_id=args.logical_id,
        deadline_seconds=args.wait_seconds,
        poll_seconds=args.poll_seconds,
    )
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    latest: Dict[str, Any] = {}
    for attempt in range(1, args.max_attempts + 1):
        latest = _annotate(
            invoke_capacity_manager(
                lambda_client=lambda_client,
                function_name=function_name,
                attempt=attempt,
                desired_multiplier=args.desired_multiplier,
                max_quota_requests=args.max_quota_requests,
                first_max_routes=args.first_max_routes,
                retry_max_routes=args.retry_max_routes,
            )
        )
        latest["capacityManagerFunctionName"] = function_name
        output_path.write_text(
            json.dumps(latest, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
        print(
            json.dumps(
                {
                    "attempt": attempt,
                    "ok": latest.get("ok"),
                    "liveCapacityOk": latest.get("liveCapacityOk"),
                    "successfulRegions": latest.get("successfulRegions"),
                    "applicationRoute": (
                        latest.get("applicationSmoke") or {}
                    ).get("routeId"),
                    "quotaIncreaseSubmittedCount": latest.get(
                        "quotaIncreaseSubmittedCount"
                    ),
                    "quotaIncreaseAcceptedOrPendingCount": latest.get(
                        "quotaIncreaseAcceptedOrPendingCount"
                    ),
                    "quotaIncreaseRequestFailureCount": latest.get(
                        "quotaIncreaseRequestFailureCount"
                    ),
                    "lambdaFunctionError": latest.get("lambdaFunctionError"),
                    "lambdaInvocationError": latest.get("lambdaInvocationError"),
                },
                indent=2,
                sort_keys=True,
                default=str,
            )
        )
        if _capacity_live(latest):
            return 0
        if attempt < args.max_attempts:
            time.sleep(max(1, int(args.retry_seconds)))
    return 2


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description=(
            "Invoke the isolated MLB AUTO Bedrock capacity manager until live "
            "capacity is proven or the bounded retry window expires."
        )
    )
    value.add_argument("--stack-name", required=True)
    value.add_argument("--region", required=True)
    value.add_argument("--logical-id", default=DEFAULT_LOGICAL_ID)
    value.add_argument("--output", required=True)
    value.add_argument("--wait-seconds", type=int, default=3600)
    value.add_argument("--poll-seconds", type=int, default=20)
    value.add_argument("--max-attempts", type=int, default=8)
    value.add_argument("--retry-seconds", type=int, default=300)
    value.add_argument("--desired-multiplier", type=float, default=10.0)
    value.add_argument("--max-quota-requests", type=int, default=30)
    value.add_argument("--first-max-routes", type=int, default=24)
    value.add_argument("--retry-max-routes", type=int, default=12)
    return value


def main() -> int:
    try:
        return run(parser().parse_args())
    except Exception as exc:
        print(f"MLB Bedrock capacity ramp failed: {type(exc).__name__}:{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
