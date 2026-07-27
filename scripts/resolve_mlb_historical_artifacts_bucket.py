#!/usr/bin/env python3
"""Resolve the MLB historical artifacts bucket without one brittle authority."""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Iterable

import boto3

STACK_NAME = "parlay-platform-mlb-historical-optimizer"
OUTPUT_KEY = "HistoricalArtifactsBucketName"
FUNCTION_OUTPUT_KEY = "HistoricalOptimizerFunctionName"
BUCKET_PREFIXES = (
    "parlay-platform-mlb-histo-historicalartifactsbucke-",
    "parlay-platform-mlb-historical-artifacts-",
)


def _nonempty(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if text and text.lower() != "none" else None


def _outputs(stack: dict[str, Any]) -> dict[str, str]:
    return {
        str(item.get("OutputKey")): str(item.get("OutputValue"))
        for item in stack.get("Outputs") or []
        if _nonempty(item.get("OutputKey")) and _nonempty(item.get("OutputValue"))
    }


def resolve_bucket(*, explicit: str | None, cloudformation: Any, lambda_client: Any, s3: Any, stack_name: str = STACK_NAME) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    explicit_value = _nonempty(explicit)
    if explicit_value:
        return {"ok": True, "bucketName": explicit_value, "authority": "EXPLICIT_ENV", "attempts": attempts}

    outputs: dict[str, str] = {}
    try:
        response = cloudformation.describe_stacks(StackName=stack_name)
        stacks = response.get("Stacks") or []
        outputs = _outputs(stacks[0]) if stacks else {}
        attempts.append({"strategy": "cloudformation", "outputs": sorted(outputs)})
    except Exception as exc:
        attempts.append({"strategy": "cloudformation", "error": f"{type(exc).__name__}:{exc}"})

    bucket = _nonempty(outputs.get(OUTPUT_KEY))
    if bucket:
        return {"ok": True, "bucketName": bucket, "authority": "CLOUDFORMATION_OUTPUT", "attempts": attempts}

    function_name = _nonempty(outputs.get(FUNCTION_OUTPUT_KEY))
    if function_name:
        try:
            config = lambda_client.get_function_configuration(FunctionName=function_name)
            environment = ((config.get("Environment") or {}).get("Variables") or {})
            bucket = _nonempty(environment.get("MLB_HISTORICAL_ARTIFACTS_BUCKET"))
            attempts.append({"strategy": "lambda_environment", "functionName": function_name, "found": bool(bucket)})
            if bucket:
                return {"ok": True, "bucketName": bucket, "authority": "LAMBDA_ENVIRONMENT", "attempts": attempts}
        except Exception as exc:
            attempts.append({"strategy": "lambda_environment", "functionName": function_name, "error": f"{type(exc).__name__}:{exc}"})
    else:
        attempts.append({"strategy": "lambda_environment", "skipped": "function_output_missing"})

    try:
        names = sorted(
            name
            for item in s3.list_buckets().get("Buckets") or []
            if (name := _nonempty(item.get("Name"))) and any(name.startswith(prefix) for prefix in BUCKET_PREFIXES)
        )
        attempts.append({"strategy": "s3_unique_prefix", "matches": names})
        if len(names) == 1:
            return {"ok": True, "bucketName": names[0], "authority": "S3_UNIQUE_PREFIX", "attempts": attempts}
        blocker = "historical_artifacts_bucket_not_found" if not names else "historical_artifacts_bucket_ambiguous"
    except Exception as exc:
        attempts.append({"strategy": "s3_unique_prefix", "error": f"{type(exc).__name__}:{exc}"})
        blocker = "historical_artifacts_bucket_resolution_failed"

    return {"ok": False, "bucketName": None, "authority": None, "blockers": [blocker], "attempts": attempts}


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", default=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION"))
    parser.add_argument("--stack-name", default=STACK_NAME)
    parser.add_argument("--json-output")
    args = parser.parse_args(list(argv) if argv is not None else None)
    session = boto3.session.Session(region_name=args.region)
    result = resolve_bucket(
        explicit=os.environ.get("MLB_HISTORICAL_ARTIFACTS_BUCKET"),
        cloudformation=session.client("cloudformation"),
        lambda_client=session.client("lambda"),
        s3=session.client("s3"),
        stack_name=args.stack_name,
    )
    payload = json.dumps(result, indent=2, sort_keys=True)
    if args.json_output:
        with open(args.json_output, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
    print(payload, file=sys.stderr)
    if result.get("ok"):
        print(result["bucketName"])
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
