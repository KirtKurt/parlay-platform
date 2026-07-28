#!/usr/bin/env python3
"""Resolve the authoritative MLB historical artifacts bucket without guessing.

Resolution order:
1. Explicit environment override.
2. CloudFormation bucket output.
3. CloudFormation optimizer-function environment.
4. Active optimizer Lambda handler scan.
5. Canonical S3 corpus probe, requiring one uniquely strongest candidate.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Iterable

import boto3

STACK_NAME = "parlay-platform-mlb-historical-optimizer"
OUTPUT_KEY = "HistoricalArtifactsBucketName"
FUNCTION_OUTPUT_KEY = "HistoricalOptimizerFunctionName"
BUCKET_ENV_KEY = "MLB_HISTORICAL_ARTIFACTS_BUCKET"
DATASET_PREFIX = "mlb/historical-daily-v1/datasets/"
OPTIMIZER_HANDLER_TOKENS = (
    "mlb_historical_optimizer_handler",
    "mlb_historical_optimizer_v7_recovery_entrypoint",
    "mlb_historical_optimizer_entrypoint",
)
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


def _environment(config: dict[str, Any]) -> dict[str, str]:
    values = ((config.get("Environment") or {}).get("Variables") or {})
    return {str(key): str(value) for key, value in values.items()}


def _lambda_scan(lambda_client: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    attempts: list[dict[str, Any]] = []
    matches: list[dict[str, Any]] = []
    marker = None
    try:
        while True:
            kwargs = {"MaxItems": 50}
            if marker:
                kwargs["Marker"] = marker
            response = lambda_client.list_functions(**kwargs)
            for summary in response.get("Functions") or []:
                handler = str(summary.get("Handler") or "")
                if not any(token in handler for token in OPTIMIZER_HANDLER_TOKENS):
                    continue
                name = _nonempty(summary.get("FunctionName"))
                if not name:
                    continue
                try:
                    config = lambda_client.get_function_configuration(FunctionName=name)
                    bucket = _nonempty(_environment(config).get(BUCKET_ENV_KEY))
                    matches.append({
                        "functionName": name,
                        "handler": str(config.get("Handler") or handler),
                        "lastModified": str(config.get("LastModified") or summary.get("LastModified") or ""),
                        "state": str(config.get("State") or summary.get("State") or ""),
                        "bucketName": bucket,
                    })
                except Exception as exc:
                    attempts.append({"strategy": "lambda_handler_scan_config", "functionName": name, "error": f"{type(exc).__name__}:{exc}"})
            marker = _nonempty(response.get("NextMarker"))
            if not marker:
                break
        attempts.append({"strategy": "lambda_handler_scan", "matches": matches})
    except Exception as exc:
        attempts.append({"strategy": "lambda_handler_scan", "error": f"{type(exc).__name__}:{exc}"})
    return matches, attempts


def _last_modified_epoch(value: Any) -> float:
    if isinstance(value, datetime):
        current = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return current.timestamp()
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _probe_bucket(s3: Any, bucket: str) -> dict[str, Any]:
    count = 0
    latest = 0.0
    continuation = None
    page_count = 0
    try:
        while True:
            kwargs: dict[str, Any] = {"Bucket": bucket, "Prefix": DATASET_PREFIX, "MaxKeys": 1000}
            if continuation:
                kwargs["ContinuationToken"] = continuation
            response = s3.list_objects_v2(**kwargs)
            page_count += 1
            objects = response.get("Contents") or []
            count += len(objects)
            for item in objects:
                latest = max(latest, _last_modified_epoch(item.get("LastModified")))
            if not response.get("IsTruncated"):
                break
            continuation = _nonempty(response.get("NextContinuationToken"))
            if not continuation or page_count >= 20:
                break
        return {
            "bucketName": bucket,
            "datasetObjectCount": count,
            "latestDatasetObjectEpoch": latest,
            "probeOk": True,
        }
    except Exception as exc:
        return {
            "bucketName": bucket,
            "datasetObjectCount": 0,
            "latestDatasetObjectEpoch": 0.0,
            "probeOk": False,
            "error": f"{type(exc).__name__}:{exc}",
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
            bucket = _nonempty(_environment(config).get(BUCKET_ENV_KEY))
            attempts.append({"strategy": "lambda_environment", "functionName": function_name, "found": bool(bucket)})
            if bucket:
                return {"ok": True, "bucketName": bucket, "authority": "LAMBDA_ENVIRONMENT", "attempts": attempts}
        except Exception as exc:
            attempts.append({"strategy": "lambda_environment", "functionName": function_name, "error": f"{type(exc).__name__}:{exc}"})
    else:
        attempts.append({"strategy": "lambda_environment", "skipped": "function_output_missing"})

    lambda_matches, scan_attempts = _lambda_scan(lambda_client)
    attempts.extend(scan_attempts)
    active = [row for row in lambda_matches if row.get("bucketName") and str(row.get("state") or "").upper() not in {"FAILED", "INACTIVE"}]
    bucket_groups: dict[str, list[dict[str, Any]]] = {}
    for row in active:
        bucket_groups.setdefault(str(row["bucketName"]), []).append(row)
    if len(bucket_groups) == 1:
        bucket = next(iter(bucket_groups))
        return {"ok": True, "bucketName": bucket, "authority": "ACTIVE_LAMBDA_HANDLER_SCAN", "attempts": attempts}
    if len(bucket_groups) > 1:
        ranked = sorted(
            bucket_groups.items(),
            key=lambda item: max(_last_modified_epoch(row.get("lastModified")) for row in item[1]),
            reverse=True,
        )
        first_time = max(_last_modified_epoch(row.get("lastModified")) for row in ranked[0][1])
        second_time = max(_last_modified_epoch(row.get("lastModified")) for row in ranked[1][1])
        if first_time > second_time:
            return {"ok": True, "bucketName": ranked[0][0], "authority": "NEWEST_ACTIVE_LAMBDA_HANDLER", "attempts": attempts}

    try:
        names = sorted(
            name
            for item in s3.list_buckets().get("Buckets") or []
            if (name := _nonempty(item.get("Name"))) and any(name.startswith(prefix) for prefix in BUCKET_PREFIXES)
        )
        attempts.append({"strategy": "s3_prefix_candidates", "matches": names})
        if len(names) == 1:
            return {"ok": True, "bucketName": names[0], "authority": "S3_UNIQUE_PREFIX", "attempts": attempts}
        if not names:
            blocker = "historical_artifacts_bucket_not_found"
        else:
            probes = [_probe_bucket(s3, name) for name in names]
            attempts.append({"strategy": "s3_canonical_corpus_probe", "probes": probes})
            usable = [row for row in probes if row.get("probeOk") and int(row.get("datasetObjectCount") or 0) > 0]
            usable.sort(key=lambda row: (int(row.get("datasetObjectCount") or 0), float(row.get("latestDatasetObjectEpoch") or 0.0)), reverse=True)
            if usable:
                top = usable[0]
                second = usable[1] if len(usable) > 1 else None
                top_score = (int(top["datasetObjectCount"]), float(top["latestDatasetObjectEpoch"]))
                second_score = (int(second["datasetObjectCount"]), float(second["latestDatasetObjectEpoch"])) if second else (-1, -1.0)
                if top_score > second_score:
                    return {"ok": True, "bucketName": top["bucketName"], "authority": "S3_CANONICAL_CORPUS_PROBE", "attempts": attempts}
            blocker = "historical_artifacts_bucket_ambiguous"
    except Exception as exc:
        attempts.append({"strategy": "s3_bucket_resolution", "error": f"{type(exc).__name__}:{exc}"})
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
        explicit=os.environ.get(BUCKET_ENV_KEY),
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
