from __future__ import annotations

import argparse
import base64
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import boto3

SENSITIVE_TOKENS = (
    "authorization",
    "credential",
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
)


def _redact(value: Any, key: str = "") -> Any:
    lowered = key.lower()
    if any(token in lowered for token in SENSITIVE_TOKENS):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str) and len(value) > 8000:
        return value[:8000] + "...[TRUNCATED]"
    return value


def _json_payload(stream: Any) -> Any:
    raw = stream.read() if hasattr(stream, "read") else stream
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {"rawPayload": str(raw)[:8000]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stack-name", default="parlay-platform-dev")
    parser.add_argument("--region", required=True)
    parser.add_argument(
        "--output",
        default="runtime_reports/mlb_trainer_runtime_error_latest.json",
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
    configuration = lam.get_function_configuration(FunctionName=function_name)
    response = lam.invoke(
        FunctionName=function_name,
        InvocationType="RequestResponse",
        LogType="Tail",
        Payload=json.dumps(
            {
                "sport": "mlb",
                "mode": "scheduled",
                "run": "diagnose_exact_trainer_contract_failure",
            }
        ).encode("utf-8"),
    )
    payload = _json_payload(response.get("Payload"))
    log_tail = ""
    if response.get("LogResult"):
        log_tail = base64.b64decode(response["LogResult"]).decode(
            "utf-8", errors="replace"
        )

    report = {
        "proofType": "MLB_TRAINER_RUNTIME_ERROR_DIAGNOSTIC",
        "version": "MLB-TRAINER-RUNTIME-ERROR-DIAGNOSTIC-v1",
        "createdAtUtc": datetime.now(timezone.utc).isoformat(),
        "stackName": args.stack_name,
        "functionName": function_name,
        "functionState": configuration.get("State"),
        "lastUpdateStatus": configuration.get("LastUpdateStatus"),
        "handler": configuration.get("Handler"),
        "runtime": configuration.get("Runtime"),
        "functionError": response.get("FunctionError"),
        "statusCode": response.get("StatusCode"),
        "executedVersion": response.get("ExecutedVersion"),
        "payload": _redact(payload),
        "logTail": _redact(log_tail),
        "secretExposed": False,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
