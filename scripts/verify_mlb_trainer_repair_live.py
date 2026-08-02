from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import boto3

EXPECTED_COMPAT_VERSION = (
    "MLB-TRAINER-CANONICAL-CONTINUITY-WAIT-v3-return-and-persist"
)
LEASE_ERROR_MESSAGE = (
    "MLB AWS trainer execution lease is unavailable for this experiment"
)


def _payload(response: Mapping[str, Any]) -> Any:
    stream = response.get("Payload")
    raw = stream.read() if hasattr(stream, "read") else stream
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {"rawPayload": str(raw)[:4000]}


def _is_retryable(response: Mapping[str, Any], body: Any) -> bool:
    if response.get("FunctionError") != "Unhandled" or not isinstance(body, Mapping):
        return False
    error_type = str(body.get("errorType") or "")
    message = str(body.get("errorMessage") or "")
    if message == LEASE_ERROR_MESSAGE and error_type in {
        "ExecutionLeaseUnavailable",
        "MLBMLExecutionLeaseUnavailable",
    }:
        return True
    return (
        error_type == "TrainingContractError"
        and message
        == "training returned an unhealthy status: CANONICAL_SLATE_CONTINUITY_BLOCKED"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", required=True)
    parser.add_argument("--stack-name", default="parlay-platform-dev")
    parser.add_argument("--deadline-seconds", type=int, default=1200)
    parser.add_argument("--retry-delay-seconds", type=int, default=20)
    parser.add_argument(
        "--output",
        default="runtime_reports/mlb_trainer_repair_live_verification_latest.json",
    )
    args = parser.parse_args()

    cf = boto3.client("cloudformation", region_name=args.region)
    lam = boto3.client("lambda", region_name=args.region)
    stack = cf.describe_stacks(StackName=args.stack_name)["Stacks"][0]
    outputs = {
        item["OutputKey"]: item["OutputValue"]
        for item in stack.get("Outputs", [])
    }
    function_name = outputs["MLBMLTrainingFunctionArn"]
    deadline = time.monotonic() + max(1, args.deadline_seconds)
    attempts: list[dict[str, Any]] = []
    final_response: Mapping[str, Any] | None = None
    final_body: Any = None

    while True:
        config = lam.get_function_configuration(FunctionName=function_name)
        response = lam.invoke(
            FunctionName=function_name,
            InvocationType="RequestResponse",
            Payload=json.dumps(
                {
                    "sport": "mlb",
                    "mode": "scheduled",
                    "run": "verify_continuity_wait_return_repair",
                }
            ).encode("utf-8"),
        )
        body = _payload(response)
        attempts.append(
            {
                "at": datetime.now(timezone.utc).isoformat(),
                "handler": config.get("Handler"),
                "lastModified": config.get("LastModified"),
                "lastUpdateStatus": config.get("LastUpdateStatus"),
                "functionError": response.get("FunctionError"),
                "status": body.get("status") if isinstance(body, Mapping) else None,
                "errorType": body.get("errorType") if isinstance(body, Mapping) else None,
                "errorMessage": body.get("errorMessage") if isinstance(body, Mapping) else None,
                "compatibilityVersion": body.get("continuityWaitCompatibilityVersion")
                if isinstance(body, Mapping)
                else None,
            }
        )
        final_response, final_body = response, body
        verified = (
            response.get("FunctionError") is None
            and isinstance(body, Mapping)
            and body.get("ok") is True
            and body.get("status") == "WAITING_FOR_CANONICAL_SLATE_CONTINUITY"
            and body.get("continuityWaitCompatibilityVersion")
            == EXPECTED_COMPAT_VERSION
            and body.get("trainingReady") is False
            and body.get("modelTrained") is False
            and body.get("championChanged") is False
            and body.get("liveInferenceAuthority") is False
            and body.get("automaticPromotionEnabled") is False
            and body.get("productionAuthorityChanged") is False
        )
        if verified:
            break
        if time.monotonic() >= deadline or not _is_retryable(response, body):
            break
        time.sleep(max(1, args.retry_delay_seconds))

    report = {
        "proofType": "MLB_TRAINER_LIVE_REPAIR_VERIFICATION",
        "version": "MLB-TRAINER-LIVE-REPAIR-VERIFICATION-v1",
        "createdAtUtc": datetime.now(timezone.utc).isoformat(),
        "stackName": args.stack_name,
        "stackStatus": stack.get("StackStatus"),
        "functionName": function_name,
        "expectedCompatibilityVersion": EXPECTED_COMPAT_VERSION,
        "attemptCount": len(attempts),
        "attempts": attempts,
        "functionError": final_response.get("FunctionError") if final_response else None,
        "result": final_body,
        "verified": bool(
            final_response
            and final_response.get("FunctionError") is None
            and isinstance(final_body, Mapping)
            and final_body.get("ok") is True
            and final_body.get("status")
            == "WAITING_FOR_CANONICAL_SLATE_CONTINUITY"
            and final_body.get("continuityWaitCompatibilityVersion")
            == EXPECTED_COMPAT_VERSION
            and final_body.get("trainingReady") is False
            and final_body.get("modelTrained") is False
            and final_body.get("championChanged") is False
            and final_body.get("liveInferenceAuthority") is False
            and final_body.get("automaticPromotionEnabled") is False
            and final_body.get("productionAuthorityChanged") is False
        ),
        "secretExposed": False,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0 if report["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
