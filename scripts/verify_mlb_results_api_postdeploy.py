from __future__ import annotations

import argparse
import ast
import base64
import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

try:
    from scripts.mlb_lambda_artifact_identity import (
        MANIFEST_SCHEMA_VERSION,
        MAX_COMPRESSED_ARTIFACT_BYTES,
        lambda_code_sha256,
        zip_content_manifest,
    )
except ModuleNotFoundError:
    from mlb_lambda_artifact_identity import (
        MANIFEST_SCHEMA_VERSION,
        MAX_COMPRESSED_ARTIFACT_BYTES,
        lambda_code_sha256,
        zip_content_manifest,
    )


PROOF_VERSION = "MLB-RESULTS-API-POSTDEPLOY-v1"
PINNED_PROBE_SLATE_DATE = "2026-08-04"
RESULTS_LOGICAL_ID = "MLBResultsSchedulerFunction"
API_LOGICAL_ID = "ServerlessRestApi"
EXPECTED_HANDLER = "mlb_results_scheduler.lambda_handler"
EXPECTED_SCHEDULE = "cron(6/15 * * * ? *)"
SCHEDULE_TO_HANDLER_MAX_AGE = timedelta(minutes=10)
RESULT_SIGNAL_PRODUCER_PROOF_VERSION = "MLB-RESULT-SIGNAL-PRODUCER-PROOF-v1"
RESULT_SIGNAL_PRODUCER_AUTHORITY = "NATIVE_EVENTBRIDGE_SCHEDULE_ENVELOPE"
EVENTBRIDGE_TARGET_INPUT_SELECTORS = {"Input", "InputPath", "InputTransformer"}
RESULT_PATHS: Tuple[str, ...] = (
    "/v1/results/mlb/final-scores",
    "/v1/results/mlb/settlement",
    "/v1/results/mlb/proof",
    "/v1/results/mlb/signal-learning",
    "/v1/results/mlb/result-signals",
)
LEGACY_RESULT_PATHS: Tuple[str, ...] = (
    "/v1/mlb/scores/final",
    "/v1/mlb/settlement/proof_report",
    "/v1/mlb/settlement/slate",
    "/v1/mlb/signal-learning",
    "/v1/mlb/result-signals",
)
EXPECTED_ROUTE_METHODS = {
    path: ("GET", "OPTIONS") for path in RESULT_PATHS
}
MAX_QUERY_PAGES = 100
MAX_QUERY_ITEMS = 20_000
MAX_HTTP_BODY_BYTES = 2_000_000
MAX_API_EXPORT_BYTES = 10_000_000
EASTERN = ZoneInfo("America/New_York")


class VerificationError(RuntimeError):
    pass


def _download_lambda_artifact(location: str) -> bytes:
    if not str(location or "").startswith("https://"):
        raise VerificationError("Results Lambda code location is not HTTPS")
    artifact = b""
    for attempt in range(1, 4):
        request = urllib.request.Request(
            location,
            headers={"User-Agent": "inqsi-mlb-results-postdeploy/1.0"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                content_length = response.headers.get("Content-Length")
                if (
                    content_length
                    and int(content_length) > MAX_COMPRESSED_ARTIFACT_BYTES
                ):
                    raise VerificationError(
                        "Results Lambda artifact exceeds download limit"
                    )
                artifact = response.read(MAX_COMPRESSED_ARTIFACT_BYTES + 1)
            break
        except urllib.error.HTTPError as exc:
            if (exc.code != 429 and not 500 <= exc.code <= 599) or attempt == 3:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == 3:
                raise
        time.sleep(2 ** (attempt - 1))
    if not artifact or len(artifact) > MAX_COMPRESSED_ARTIFACT_BYTES:
        raise VerificationError("Results Lambda artifact is empty or oversized")
    return artifact


def verify_deployed_code_artifact(
    function: Mapping[str, Any],
    *,
    expected_build_manifest: Mapping[str, Any],
    deploy_identity: Mapping[str, Any],
    expected_deploy_sha: str,
    expected_template_sha256: str,
    expected_deploy_run_id: str,
) -> Dict[str, Any]:
    functions = expected_build_manifest.get("functions") or {}
    expected_content = functions.get(RESULTS_LOGICAL_ID)
    if not (
        expected_build_manifest.get("schemaVersion") == MANIFEST_SCHEMA_VERSION
        and expected_build_manifest.get("expectedGitSha") == expected_deploy_sha
        and expected_build_manifest.get("expectedTemplateSha256")
        == expected_template_sha256
        and isinstance(expected_content, dict)
    ):
        raise VerificationError("Exact deploy build manifest identity mismatch")
    identity_function = (
        (deploy_identity.get("functions") or {}).get(RESULTS_LOGICAL_ID) or {}
    )
    if not (
        deploy_identity.get("ok") is True
        and deploy_identity.get("expectedGitSha") == expected_deploy_sha
        and deploy_identity.get("expectedTemplateSha256")
        == expected_template_sha256
        and deploy_identity.get("expectedDeployRunId") == expected_deploy_run_id
        and identity_function.get("identityMatches") is True
        and identity_function.get("codeArtifactMatchesCleanBuild") is True
        and identity_function.get("expectedCodeContentManifest") == expected_content
        and identity_function.get("deployedCodeContentManifest") == expected_content
    ):
        raise VerificationError("Triggering deploy artifact attestation mismatch")

    configuration = function.get("Configuration") or {}
    configured_code_sha = str(configuration.get("CodeSha256") or "")
    artifact = _download_lambda_artifact(
        str((function.get("Code") or {}).get("Location") or "")
    )
    downloaded_code_sha = lambda_code_sha256(artifact)
    if configured_code_sha != downloaded_code_sha:
        raise VerificationError(
            "Current Results Lambda CodeSha256 differs from its downloadable artifact"
        )
    current_content = zip_content_manifest(artifact)
    if current_content != expected_content:
        raise VerificationError(
            "Current Results Lambda code differs from the exact clean deploy build"
        )
    if identity_function.get("codeSha256") != configured_code_sha:
        raise VerificationError(
            "Current Results Lambda CodeSha256 differs from triggering deploy proof"
        )
    return {
        "manifestSchemaVersion": MANIFEST_SCHEMA_VERSION,
        "expectedDeploySha": expected_deploy_sha,
        "expectedDeployRunId": expected_deploy_run_id,
        "configuredCodeSha256": configured_code_sha,
        "downloadedCodeSha256": downloaded_code_sha,
        "expectedContentManifest": expected_content,
        "currentContentManifest": current_content,
        "matchesExactTriggeringDeployArtifact": True,
    }


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return {"decimal": str(value)}
    if isinstance(value, bytes):
        return {"bytesBase64": base64.b64encode(value).decode("ascii")}
    if isinstance(value, set):
        return sorted((_json_value(item) for item in value), key=_canonical_json)
    if isinstance(value, Mapping):
        return {
            str(key): _json_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, datetime):
        return _iso(value)
    return value


def _typed_value(value: Any) -> Any:
    """Encode values without cross-type or wrapper/map fingerprint collisions."""
    if value is None:
        return ["null"]
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, Decimal):
        return ["decimal", str(value)]
    if isinstance(value, int):
        return ["int", str(value)]
    if isinstance(value, float):
        return ["float", repr(value)]
    if isinstance(value, str):
        return ["string", value]
    if isinstance(value, bytes):
        return ["bytes", base64.b64encode(value).decode("ascii")]
    if isinstance(value, datetime):
        return ["datetime", _iso(value)]
    if isinstance(value, Mapping):
        return [
            "map",
            [
                [_typed_value(str(key)), _typed_value(item)]
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            ],
        ]
    if isinstance(value, (list, tuple)):
        return ["list", [_typed_value(item) for item in value]]
    if isinstance(value, set):
        encoded = [_typed_value(item) for item in value]
        return [
            "set",
            sorted(
                encoded,
                key=lambda item: json.dumps(
                    item,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ),
            ),
        ]
    raise TypeError(f"Unsupported canonical fingerprint type: {type(value).__name__}")


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _typed_value(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _base_lambda_arn(value: Any) -> str:
    text = str(value or "")
    parts = text.split(":")
    if len(parts) >= 8 and parts[5] == "function":
        return ":".join(parts[:7])
    return text


def _integration_lambda_arn(uri: Any) -> str:
    text = urllib.parse.unquote(str(uri or ""))
    marker = "/functions/"
    suffix = "/invocations"
    if marker not in text or suffix not in text:
        return ""
    candidate = text.split(marker, 1)[1].rsplit(suffix, 1)[0]
    return candidate.strip()


def _parse_json_object(value: Any, *, label: str) -> Dict[str, Any]:
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise VerificationError(f"{label} is not JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise VerificationError(f"{label} is not a JSON object")
    return value


def _resource_physical_id(cfn: Any, stack_name: str, logical_id: str) -> str:
    response = cfn.describe_stack_resource(
        StackName=stack_name,
        LogicalResourceId=logical_id,
    )
    value = str(
        (response.get("StackResourceDetail") or {}).get("PhysicalResourceId")
        or ""
    )
    if not value or value == "None":
        raise VerificationError(f"CloudFormation resource missing: {logical_id}")
    return value


def _stack_output(cfn: Any, stack_name: str, output_key: str) -> str:
    stacks = cfn.describe_stacks(StackName=stack_name).get("Stacks") or []
    if len(stacks) != 1:
        raise VerificationError("Expected exactly one CloudFormation stack")
    status = str(stacks[0].get("StackStatus") or "")
    if status != "UPDATE_COMPLETE":
        raise VerificationError(f"Stack is not stable: {status}")
    matches = [
        str(row.get("OutputValue") or "")
        for row in stacks[0].get("Outputs") or []
        if row.get("OutputKey") == output_key
    ]
    if len(matches) != 1 or not matches[0]:
        raise VerificationError(f"CloudFormation output missing: {output_key}")
    return matches[0]


def _all_api_resources(apigateway: Any, rest_api_id: str) -> List[Dict[str, Any]]:
    resources: List[Dict[str, Any]] = []
    position: Optional[str] = None
    seen_positions = set()
    for _ in range(100):
        kwargs: Dict[str, Any] = {
            "restApiId": rest_api_id,
            "limit": 500,
            "embed": ["methods"],
        }
        if position:
            kwargs["position"] = position
        response = apigateway.get_resources(**kwargs)
        resources.extend(response.get("items") or [])
        next_position = response.get("position")
        if not next_position:
            return resources
        if next_position in seen_positions:
            raise VerificationError("API Gateway resource pagination cursor repeated")
        seen_positions.add(next_position)
        position = str(next_position)
    raise VerificationError("API Gateway resource pagination exceeded 100 pages")


def verify_api_surface(
    apigateway: Any,
    *,
    rest_api_id: str,
    function_arn: str,
) -> Dict[str, Any]:
    resources = _all_api_resources(apigateway, rest_api_id)
    path_rows: Dict[str, Dict[str, Any]] = {}
    integrations_to_results: List[Tuple[str, str]] = []
    integration_rows: List[Dict[str, Any]] = []

    for resource in resources:
        path = str(resource.get("path") or "")
        resource_id = str(resource.get("id") or "")
        methods = sorted(str(method).upper() for method in (resource.get("resourceMethods") or {}))
        if path in EXPECTED_ROUTE_METHODS:
            if path in path_rows:
                raise VerificationError(f"Duplicate API Gateway path: {path}")
            path_rows[path] = {
                "resourceId": resource_id,
                "methods": methods,
            }
        for method in methods:
            integration = apigateway.get_integration(
                restApiId=rest_api_id,
                resourceId=resource_id,
                httpMethod=method,
            )
            integration_arn = _integration_lambda_arn(integration.get("uri"))
            targets_results = integration_arn == function_arn
            if targets_results:
                integrations_to_results.append((path, method))
                integration_rows.append(
                    {
                        "path": path,
                        "method": method,
                        "type": integration.get("type"),
                        "integrationHttpMethod": integration.get("httpMethod"),
                        "integrationFunctionArn": integration_arn,
                        "targetsResultsScheduler": True,
                    }
                )

    actual_route_methods = {
        path: tuple(row["methods"])
        for path, row in sorted(path_rows.items())
    }
    expected_route_methods = {
        path: tuple(sorted(methods))
        for path, methods in EXPECTED_ROUTE_METHODS.items()
    }
    if actual_route_methods != expected_route_methods:
        raise VerificationError(
            "Live results API method surface mismatch: "
            f"expected={expected_route_methods} actual={actual_route_methods}"
        )

    expected_pairs = sorted(
        (path, method)
        for path, methods in EXPECTED_ROUTE_METHODS.items()
        for method in methods
    )
    actual_pairs = sorted(integrations_to_results)
    if actual_pairs != expected_pairs:
        raise VerificationError(
            "Results Lambda has an unexpected API integration surface: "
            f"expected={expected_pairs} actual={actual_pairs}"
        )
    if any(
        row.get("type") != "AWS_PROXY"
        or row.get("integrationHttpMethod") != "POST"
        for row in integration_rows
    ):
        raise VerificationError("A results API route is not an AWS_PROXY Lambda integration")

    return {
        "apiResourceCount": len(resources),
        "routeMethods": actual_route_methods,
        "resultsSchedulerIntegrations": sorted(
            integration_rows,
            key=lambda row: (str(row.get("path")), str(row.get("method"))),
        ),
        "exactFiveGetFiveOptionsNoPost": True,
    }


def verify_deployed_stage(
    apigateway: Any,
    *,
    rest_api_id: str,
    function_arn: str,
    stage_name: str = "Prod",
) -> Dict[str, Any]:
    stage = apigateway.get_stage(restApiId=rest_api_id, stageName=stage_name)
    deployment_id = str(stage.get("deploymentId") or "")
    if not deployment_id:
        raise VerificationError(f"API Gateway stage has no deployment: {stage_name}")
    deployment = apigateway.get_deployment(
        restApiId=rest_api_id,
        deploymentId=deployment_id,
        embed=["apisummary"],
    )
    api_summary = deployment.get("apiSummary") or {}
    if not isinstance(api_summary, dict) or not api_summary:
        raise VerificationError("Deployed API Gateway stage has no API summary")
    exported = apigateway.get_export(
        restApiId=rest_api_id,
        stageName=stage_name,
        exportType="oas30",
        parameters={"extensions": "integrations"},
        accepts="application/json",
    )
    stream = exported.get("body")
    raw_export = stream.read(MAX_API_EXPORT_BYTES + 1) if hasattr(stream, "read") else stream
    if not isinstance(raw_export, (bytes, bytearray)):
        raise VerificationError("Prod-stage API export body is not bytes")
    if len(raw_export) > MAX_API_EXPORT_BYTES:
        raise VerificationError("Prod-stage API export exceeded bounded body limit")
    export = _parse_json_object(raw_export, label="Prod-stage OpenAPI export")
    exported_paths = export.get("paths") or {}
    if not isinstance(exported_paths, dict):
        raise VerificationError("Prod-stage OpenAPI export paths is not an object")
    http_method_names = {
        "get",
        "options",
        "post",
        "put",
        "patch",
        "delete",
        "head",
        "trace",
        "connect",
        "x-amazon-apigateway-any-method",
    }
    exported_actual: Dict[str, Tuple[str, ...]] = {}
    exported_results_integrations: List[Tuple[str, str, str]] = []
    for path, path_item in exported_paths.items():
        if not isinstance(path_item, dict):
            continue
        methods = tuple(
            sorted(
                str(method).upper()
                for method in path_item
                if str(method).lower() in http_method_names
            )
        )
        if path in RESULT_PATHS:
            exported_actual[str(path)] = methods
        for method, operation in path_item.items():
            if str(method).lower() not in http_method_names or not isinstance(operation, dict):
                continue
            integration = operation.get("x-amazon-apigateway-integration") or {}
            integration_arn = _integration_lambda_arn(integration.get("uri"))
            if integration_arn == function_arn:
                exported_results_integrations.append(
                    (str(path), str(method).upper(), str(integration.get("type") or ""))
                )
    actual = {}
    for path in RESULT_PATHS:
        methods = tuple(
            sorted(
                str(method).upper()
                for method in ((api_summary.get(path) or {}).keys())
            )
        )
        actual[path] = methods
    expected = {
        path: tuple(sorted(methods))
        for path, methods in EXPECTED_ROUTE_METHODS.items()
    }
    if actual != expected:
        raise VerificationError(
            "Deployed Prod-stage results method surface mismatch: "
            f"expected={expected} actual={actual}"
        )
    if exported_actual != expected:
        raise VerificationError(
            "Exported Prod-stage results method surface mismatch: "
            f"expected={expected} actual={exported_actual}"
        )
    expected_pairs = sorted(
        (path, method, "aws_proxy")
        for path, methods in EXPECTED_ROUTE_METHODS.items()
        for method in methods
    )
    if sorted(exported_results_integrations) != expected_pairs:
        raise VerificationError(
            "Exported Prod-stage results integration surface mismatch: "
            f"expected={expected_pairs} actual={sorted(exported_results_integrations)}"
        )
    banned_post_paths = []
    for path, methods in api_summary.items():
        normalized_path = str(path)
        normalized_methods = {str(method).upper() for method in (methods or {})}
        in_results_namespace = normalized_path.startswith("/v1/results/mlb/")
        is_legacy_results_path = normalized_path in LEGACY_RESULT_PATHS
        if "POST" in normalized_methods and (in_results_namespace or is_legacy_results_path):
            banned_post_paths.append(normalized_path)
    if banned_post_paths:
        raise VerificationError(
            "Deployed Prod stage still contains an MLB results POST route: "
            + ",".join(sorted(banned_post_paths))
        )
    if stage.get("cacheClusterEnabled") is True:
        raise VerificationError("Prod-stage API caching is enabled")
    cached_settings = [
        key
        for key, value in (stage.get("methodSettings") or {}).items()
        if isinstance(value, dict) and value.get("cachingEnabled") is True
    ]
    if cached_settings:
        raise VerificationError(
            "Prod-stage method caching is enabled: " + ",".join(sorted(cached_settings))
        )
    return {
        "stageName": stage_name,
        "deploymentId": deployment_id,
        "deploymentCreatedDate": deployment.get("createdDate"),
        "stageLastUpdatedDate": stage.get("lastUpdatedDate"),
        "cacheClusterEnabled": bool(stage.get("cacheClusterEnabled")),
        "cachedMethodSettings": [],
        "routeMethods": actual,
        "exportedRouteMethods": exported_actual,
        "exportedResultsSchedulerIntegrations": exported_results_integrations,
        "exportSha256": hashlib.sha256(raw_export).hexdigest(),
        "legacyResultsPostRoutes": [],
        "exactFiveGetFiveOptionsNoPost": True,
    }


def verify_api_url(
    api_url: str,
    *,
    rest_api_id: str,
    region: str,
    stage_name: str,
) -> Dict[str, Any]:
    parsed = urllib.parse.urlparse(api_url)
    expected_host = f"{rest_api_id}.execute-api.{region}.amazonaws.com"
    expected_path = f"/{stage_name}/"
    if (
        parsed.scheme != "https"
        or parsed.hostname != expected_host
        or parsed.path != expected_path
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise VerificationError(
            "ApiUrl does not bind the exact REST API, region, and stage: "
            f"{api_url}"
        )
    return {
        "scheme": parsed.scheme,
        "host": parsed.hostname,
        "stagePath": parsed.path,
        "exact": True,
    }


def _all_rules(events: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    token: Optional[str] = None
    seen = set()
    for _ in range(100):
        kwargs = {"Limit": 100}
        if token:
            kwargs["NextToken"] = token
        response = events.list_rules(**kwargs)
        rows.extend(response.get("Rules") or [])
        next_token = response.get("NextToken")
        if not next_token:
            return rows
        if next_token in seen:
            raise VerificationError("EventBridge rule pagination cursor repeated")
        seen.add(next_token)
        token = str(next_token)
    raise VerificationError("EventBridge rule pagination exceeded 100 pages")


def _all_targets(events: Any, rule_name: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    token: Optional[str] = None
    seen = set()
    for _ in range(100):
        kwargs: Dict[str, Any] = {"Rule": rule_name, "Limit": 100}
        if token:
            kwargs["NextToken"] = token
        response = events.list_targets_by_rule(**kwargs)
        rows.extend(response.get("Targets") or [])
        next_token = response.get("NextToken")
        if not next_token:
            return rows
        if next_token in seen:
            raise VerificationError("EventBridge target pagination cursor repeated")
        seen.add(next_token)
        token = str(next_token)
    raise VerificationError("EventBridge target pagination exceeded 100 pages")


def verify_schedule(events: Any, *, function_arn: str) -> Dict[str, Any]:
    matches: List[Dict[str, Any]] = []
    for rule in _all_rules(events):
        name = str(rule.get("Name") or "")
        targets = _all_targets(events, name)
        matching = [
            target
            for target in targets
            if _base_lambda_arn(target.get("Arn")) == _base_lambda_arn(function_arn)
        ]
        if not matching:
            continue
        matches.append(
            {
                "name": name,
                "arn": rule.get("Arn"),
                "state": rule.get("State"),
                "scheduleExpression": rule.get("ScheduleExpression"),
                "eventPattern": rule.get("EventPattern"),
                "targets": matching,
                "allTargetCount": len(targets),
            }
        )
    if len(matches) != 1:
        raise VerificationError(
            f"Expected one EventBridge rule targeting results scheduler, found {len(matches)}"
        )
    match = matches[0]
    if match["state"] != "ENABLED":
        raise VerificationError("Results scheduler EventBridge rule is not enabled")
    if match["scheduleExpression"] != EXPECTED_SCHEDULE:
        raise VerificationError(
            f"Results scheduler cadence mismatch: {match['scheduleExpression']}"
        )
    if match.get("eventPattern") not in {None, ""}:
        raise VerificationError("Results scheduler rule unexpectedly has an event pattern")
    rule_arn = str(match.get("arn") or "")
    rule_arn_parts = rule_arn.split(":", 5)
    if (
        len(rule_arn_parts) != 6
        or rule_arn_parts[2] != "events"
        or rule_arn_parts[5] != f"rule/{match['name']}"
    ):
        raise VerificationError("Results scheduler rule ARN/name binding is invalid")
    if len(match["targets"]) != 1 or match["allTargetCount"] != 1:
        raise VerificationError("Results scheduler rule target topology is not one-to-one")
    target = match["targets"][0]
    if target.get("Arn") != function_arn:
        raise VerificationError("Results scheduler target is qualified or stale")
    if target.get("DeadLetterConfig"):
        raise VerificationError("Results scheduler target unexpectedly has a dead-letter queue")
    target_input_selectors = sorted(
        EVENTBRIDGE_TARGET_INPUT_SELECTORS.intersection(target)
    )
    if target_input_selectors:
        raise VerificationError(
            "Results scheduler target suppresses native EventBridge provenance: "
            f"{target_input_selectors}"
        )
    retry = target.get("RetryPolicy") or {}
    if retry != {
        "MaximumEventAgeInSeconds": 300,
        "MaximumRetryAttempts": 0,
    }:
        raise VerificationError(f"Results scheduler retry policy mismatch: {retry}")
    return {
        "ruleName": match["name"],
        "ruleArn": rule_arn,
        "state": match["state"],
        "scheduleExpression": match["scheduleExpression"],
        "eventPattern": None,
        "inputMode": "NATIVE_EVENTBRIDGE_ENVELOPE",
        "targetInputSelectorsAbsent": True,
        "retryPolicy": retry,
        "targetArn": target.get("Arn"),
        "deadLetterQueueAbsent": True,
        "exact": True,
    }


def _bounded_query(table: Any, partition_key: str) -> List[Dict[str, Any]]:
    from boto3.dynamodb.conditions import Key

    rows: List[Dict[str, Any]] = []
    exclusive_start_key: Optional[Dict[str, Any]] = None
    seen_cursors = set()
    for _ in range(MAX_QUERY_PAGES):
        kwargs: Dict[str, Any] = {
            "KeyConditionExpression": Key("PK").eq(partition_key),
            "ConsistentRead": True,
        }
        if exclusive_start_key:
            kwargs["ExclusiveStartKey"] = exclusive_start_key
        response = table.query(**kwargs)
        items = response.get("Items") or []
        if not isinstance(items, list):
            raise VerificationError("DynamoDB query Items is not a list")
        rows.extend(items)
        if len(rows) > MAX_QUERY_ITEMS:
            raise VerificationError(
                f"DynamoDB partition exceeds {MAX_QUERY_ITEMS} items: {partition_key}"
            )
        next_key = response.get("LastEvaluatedKey")
        if not next_key:
            return sorted(
                rows,
                key=lambda row: (str(row.get("PK") or ""), str(row.get("SK") or "")),
            )
        cursor = _canonical_json(next_key)
        if cursor in seen_cursors:
            raise VerificationError(
                f"DynamoDB pagination cursor repeated: {partition_key}"
            )
        seen_cursors.add(cursor)
        exclusive_start_key = next_key
    raise VerificationError(
        f"DynamoDB partition exceeds {MAX_QUERY_PAGES} pages: {partition_key}"
    )


def authoritative_tracked_partitions(
    probe_slate_date: str,
) -> Dict[str, Tuple[str, ...]]:
    try:
        probe_day = date.fromisoformat(probe_slate_date)
    except ValueError as exc:
        raise VerificationError("Probe slate date is not ISO YYYY-MM-DD") from exc
    return {
        "OutcomesTable": (
            f"MLB_CANONICAL_FINAL_LABEL#{probe_day.isoformat()}",
            f"OUTCOME#mlb#{probe_day.isoformat()}",
        ),
        "PredictionsTable": (f"PRED#mlb#{probe_day.isoformat()}",),
        "SignalLedgerTable": (f"RESULT_SIGNAL#mlb#{probe_day.isoformat()}",),
    }


def diagnostic_current_partitions(today_et: str) -> Dict[str, Tuple[str, ...]]:
    try:
        today = date.fromisoformat(today_et)
    except ValueError as exc:
        raise VerificationError("Current slate date is not ISO YYYY-MM-DD") from exc
    # These canaries expose unexpected current/recent activity, but recurring
    # producers legitimately write them.  They are never an HTTP mutation gate
    # and never support causal attribution.
    recent_dates = [today - timedelta(days=offset) for offset in range(7)]
    outcome_pks = {
        *(f"OUTCOME#mlb#{day.isoformat()}" for day in recent_dates),
        *(f"MLB_CANONICAL_FINAL_LABEL#{day.isoformat()}" for day in recent_dates),
    }
    prediction_pks = {
        *(f"PRED#mlb#{day.isoformat()}" for day in recent_dates),
    }
    result_signal_pks = {
        *(f"RESULT_SIGNAL#mlb#{day.isoformat()}" for day in recent_dates),
    }
    return {
        "OutcomesTable": tuple(sorted(outcome_pks)),
        "PredictionsTable": tuple(sorted(prediction_pks)),
        "SignalLedgerTable": tuple(sorted(result_signal_pks)),
    }


def snapshot_partitions(
    tables: Mapping[str, Any],
    partitions: Mapping[str, Sequence[str]],
) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for logical_id, pks in sorted(partitions.items()):
        table = tables[logical_id]
        partition_rows = {}
        for pk in pks:
            items = _bounded_query(table, pk)
            partition_rows[pk] = {
                "count": len(items),
                "fingerprint": _sha256(items),
            }
        result[logical_id] = {
            "tableName": table.name,
            "partitions": partition_rows,
        }
    return result


def snapshot_partitions_diagnostic(
    tables: Mapping[str, Any],
    partitions: Mapping[str, Sequence[str]],
) -> Dict[str, Any]:
    """Best-effort canaries that must never gate the pinned-slate proof."""

    result: Dict[str, Any] = {}
    for logical_id, pks in sorted(partitions.items()):
        table = tables[logical_id]
        partition_rows = {}
        for pk in pks:
            try:
                items = _bounded_query(table, pk)
                partition_rows[pk] = {
                    "available": True,
                    "count": len(items),
                    "fingerprint": _sha256(items),
                }
            except Exception as exc:
                partition_rows[pk] = {
                    "available": False,
                    "error": f"{type(exc).__name__}:{exc}",
                }
        result[logical_id] = {
            "tableName": table.name,
            "partitions": partition_rows,
            "authoritative": False,
        }
    return result


def partition_snapshot_changes(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> List[Dict[str, Any]]:
    changes: List[Dict[str, Any]] = []
    logical_ids = sorted(set(before) | set(after))
    for logical_id in logical_ids:
        left = (before.get(logical_id) or {}).get("partitions") or {}
        right = (after.get(logical_id) or {}).get("partitions") or {}
        for pk in sorted(set(left) | set(right)):
            if left.get(pk) != right.get(pk):
                changes.append(
                    {
                        "table": logical_id,
                        "partition": pk,
                        "before": left.get(pk),
                        "after": right.get(pk),
                    }
                )
    return changes


def verify_snapshots_unchanged(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    table_wide_write_diagnostic: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    changes = partition_snapshot_changes(before, after)
    if changes:
        raise VerificationError(
            "A pinned historical protected partition changed during observation; "
            "public/direct HTTP read-only behavior was not proven: "
            + _canonical_json(changes)
        )
    proof: Dict[str, Any] = {
        "protectedPartitionsUnchanged": True,
        "mutationProofAuthority": (
            "STRONG_CONSISTENT_PINNED_HISTORICAL_PARTITION_FINGERPRINT_EQUALITY"
        ),
        "pinnedHistoricalSlateOutsideRecurringWriterAuthority": True,
        "netPartitionStateInvariant": True,
        "individualWriteCallsObservedOrAttributed": False,
        "tableWideWriteMetricsAuthoritative": False,
    }
    if table_wide_write_diagnostic is not None:
        proof["nonAuthoritativeTableWideWriteDiagnostic"] = dict(
            table_wide_write_diagnostic
        )
    return proof


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_HTTP_OPENER = urllib.request.build_opener(_RejectRedirects())


def _read_http_response(response: Any) -> Tuple[int, Dict[str, str], bytes, str]:
    body = response.read(MAX_HTTP_BODY_BYTES + 1)
    if len(body) > MAX_HTTP_BODY_BYTES:
        raise VerificationError("HTTP response exceeded bounded body limit")
    headers = {str(key).lower(): str(value) for key, value in response.headers.items()}
    return int(response.status), headers, body, str(response.geturl() or "")


def _http_request(
    url: str,
    method: str,
    *,
    body: Optional[bytes] = None,
    timeout: int = 60,
) -> Dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "accept": "application/json",
            "content-type": "application/json",
            "origin": "https://inqsi.com",
            "user-agent": "inqsi-results-read-only-postdeploy/1.0",
        },
    )
    try:
        with _HTTP_OPENER.open(request, timeout=timeout) as response:
            status, headers, body, final_url = _read_http_response(response)
    except urllib.error.HTTPError as exc:
        status, headers, body, final_url = _read_http_response(exc)
    if final_url != url:
        raise VerificationError(
            f"HTTP response URL changed: expected={url} actual={final_url}"
        )
    return {
        "status": status,
        "headers": headers,
        "bodyBytes": len(body),
        "bodySha256": hashlib.sha256(body).hexdigest(),
        "finalUrlMatchesRequest": True,
        "json": _parse_json_object(body, label=f"{method} {url} response"),
    }


def _cors_methods(headers: Mapping[str, str]) -> Tuple[str, ...]:
    value = str(headers.get("access-control-allow-methods") or "")
    return tuple(sorted(part.strip().upper() for part in value.split(",") if part.strip()))


def verify_public_get_contract(
    path: str,
    response: Mapping[str, Any],
    *,
    probe_slate_date: str,
) -> Dict[str, Any]:
    status = int(response.get("status") or 0)
    body = response.get("json")
    headers = response.get("headers") or {}
    if not isinstance(body, dict):
        raise VerificationError(f"GET {path} did not return an object")
    if not str(headers.get("content-type") or "").lower().startswith(
        "application/json"
    ):
        raise VerificationError(f"GET {path} did not return application/json")
    if body.get("sport") != "mlb":
        raise VerificationError(f"GET {path} response is not MLB-scoped")

    if path == "/v1/results/mlb/final-scores":
        if status != 200 or body.get("ok") is not True:
            raise VerificationError("Final-scores GET did not return the read model")
        if body.get("slate_date_et") != probe_slate_date:
            raise VerificationError("Final-scores GET returned the wrong slate")
        fetch = body.get("fetch_report") or {}
        if fetch.get("skipped") is not True or fetch.get("reason") != "fetch_scores_false":
            raise VerificationError("Final-scores GET did not prove live score fetch was disabled")
        if not isinstance(body.get("final_scores"), list):
            raise VerificationError("Final-scores GET payload lacks a stored score list")
        return {"contract": "STORED_FINAL_SCORES_READ_ONLY", "valid": True}

    if path in {
        "/v1/results/mlb/settlement",
        "/v1/results/mlb/proof",
    }:
        expected_status = 200 if body.get("ok") is True else 409
        if status != expected_status:
            raise VerificationError(
                f"GET {path} status does not match canonical result: {status}"
            )
        if (body.get("slateDateEt") or body.get("slate_date_et")) != probe_slate_date:
            raise VerificationError(f"GET {path} returned the wrong slate")
        legacy = body.get("legacyDiagnosticCompatibility") or {}
        if legacy != {
            "ok": True,
            "executed": False,
            "authoritative": False,
            "status": "LEGACY_DIAGNOSTIC_DISABLED",
        }:
            raise VerificationError(f"GET {path} did not hard-disable legacy mutation")
        if body.get("settlementAuthority") != "CANONICAL_IMMUTABLE_LOCK_OFFICIAL_GAME_PK":
            raise VerificationError(f"GET {path} returned the wrong settlement authority")
        if body.get("legacyDiagnosticIsAuthoritative") is not False:
            raise VerificationError(f"GET {path} elevated legacy settlement")
        if body.get("immutablePregameRowsMutated") is not False:
            raise VerificationError(f"GET {path} reports a pregame mutation")
        if int(body.get("labelCreatedCount") or 0) != 0:
            raise VerificationError(f"GET {path} created a canonical label")
        if path.endswith("/proof") and body.get("readOnlyProof") is not True:
            raise VerificationError("Settlement proof GET lacks readOnlyProof=true")
        return {
            "contract": (
                "CANONICAL_SETTLEMENT_READ_ONLY_PROOF"
                if path.endswith("/proof")
                else "CANONICAL_SETTLEMENT_DRY_RUN"
            ),
            "valid": True,
            "canonicalOk": body.get("ok") is True,
        }

    if path == "/v1/results/mlb/signal-learning":
        if status != 200 or body.get("ok") is not True:
            raise VerificationError("Signal-learning GET did not return its read model")
        if body.get("slate_date_et") != probe_slate_date:
            raise VerificationError("Signal-learning GET returned the wrong slate")
        score_fetch = body.get("score_fetch") or {}
        if score_fetch.get("fetch_scores") is not False:
            raise VerificationError("Signal-learning GET enabled score ingestion")
        fetch = score_fetch.get("fetch_report") or {}
        if fetch.get("skipped") is not True or fetch.get("reason") != "fetch_scores_false":
            raise VerificationError("Signal-learning GET did not prove score fetch was skipped")
        if body.get("deployment_safe") is not True:
            raise VerificationError("Signal-learning GET is not marked observe-only safe")
        return {"contract": "OBSERVE_ONLY_SIGNAL_LEARNING", "valid": True}

    if path == "/v1/results/mlb/result-signals":
        if status != 200 or body.get("ok") is not True:
            raise VerificationError("Result-signals GET did not return its latest-read model")
        if body.get("game_date_et") != probe_slate_date:
            raise VerificationError("Result-signals GET returned the wrong slate")
        items = body.get("items")
        if not isinstance(items, list) or int(body.get("count") or 0) != len(items):
            raise VerificationError("Result-signals GET count/items contract is invalid")
        if "stored_rows" in body or "result_signal_rows" in body:
            raise VerificationError("Result-signals GET returned the mutating build schema")
        return {"contract": "LATEST_RESULT_SIGNALS_READ_ONLY", "valid": True}

    raise VerificationError(f"No public GET contract defined for {path}")


def invoke_public_surface(
    *,
    api_url: str,
    probe_slate_date: str,
) -> List[Dict[str, Any]]:
    base = api_url.rstrip("/")
    query = urllib.parse.urlencode(
        {
            "date": probe_slate_date,
            "days_from": "0",
            "fetch_scores": "true",
            "store": "true",
            "build": "true",
            "legacy_diagnostic": "true",
            "proof_nonce": hashlib.sha256(
                f"{time.time_ns()}:{probe_slate_date}".encode("utf-8")
            ).hexdigest(),
        }
    )
    rows: List[Dict[str, Any]] = []
    hostile_body = json.dumps(
        {
            "days_from": "not-an-integer",
            "fetch_scores": True,
            "store": True,
            "build": True,
            "legacy_diagnostic": True,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    for path in RESULT_PATHS:
        options = _http_request(base + path, "OPTIONS")
        if options["status"] != 200:
            raise VerificationError(f"OPTIONS {path} returned {options['status']}")
        methods = _cors_methods(options["headers"])
        if methods != ("GET", "OPTIONS"):
            raise VerificationError(f"OPTIONS {path} advertised methods {methods}")
        if options["headers"].get("access-control-allow-origin") != "*":
            raise VerificationError(f"OPTIONS {path} lacks exact CORS origin")
        rows.append(
            {
                "path": path,
                "method": "OPTIONS",
                "status": options["status"],
                "corsMethods": methods,
                "bodyBytes": options["bodyBytes"],
                "bodySha256": options["bodySha256"],
            }
        )

        get = _http_request(base + path + "?" + query, "GET")
        if _cors_methods(get["headers"]) != ("GET", "OPTIONS"):
            raise VerificationError(f"GET {path} advertised a mutating method")
        if get["headers"].get("access-control-allow-origin") != "*":
            raise VerificationError(f"GET {path} lacks exact CORS origin")
        contract = verify_public_get_contract(
            path,
            get,
            probe_slate_date=probe_slate_date,
        )
        rows.append(
            {
                "path": path,
                "method": "GET",
                "status": get["status"],
                "corsMethods": _cors_methods(get["headers"]),
                "bodyBytes": get["bodyBytes"],
                "bodySha256": get["bodySha256"],
                "ok": get["json"].get("ok"),
                "error": get["json"].get("error"),
                "responseContract": contract,
            }
        )

        post = _http_request(base + path, "POST", body=hostile_body)
        if post["status"] != 403:
            raise VerificationError(
                f"Public POST route exists or has unexpected behavior for {path}: "
                f"status={post['status']}"
            )
        rows.append(
            {
                "path": path,
                "method": "POST",
                "status": post["status"],
                "routeAbsentAtPublicStage": True,
                "bodyBytes": post["bodyBytes"],
                "bodySha256": post["bodySha256"],
            }
        )
    return rows


def _lambda_payload(response: Mapping[str, Any]) -> Dict[str, Any]:
    stream = response.get("Payload")
    raw = stream.read() if hasattr(stream, "read") else stream
    if response.get("FunctionError"):
        raise VerificationError(
            f"Direct HTTP gate invocation raised {response.get('FunctionError')}: {raw!r}"
        )
    return _parse_json_object(raw, label="direct Lambda response")


def hostile_http_envelopes() -> List[Tuple[str, Dict[str, Any]]]:
    """Build hostile API envelopes with one unambiguous method source each."""

    envelopes: List[Tuple[str, Dict[str, Any]]] = []
    malformed_body = json.dumps(
        {
            "days_from": "not-an-integer",
            "store": True,
            "build": True,
            "fetch_scores": True,
            "legacy_diagnostic": True,
        }
    )
    for path in RESULT_PATHS:
        envelopes.append(
            (
                f"rest-v1-post:{path}",
                dict(
                    httpMethod="POST",
                    path=path,
                    requestContext={"apiId": "hostile-direct-proof"},
                    body=malformed_body,
                ),
            )
        )
    envelopes.extend(
        [
            (
                "http-v2-post",
                {
                    "version": "2.0",
                    "rawPath": RESULT_PATHS[-1],
                    "requestContext": {
                        "apiId": "hostile-direct-proof",
                        "http": {"method": "POST", "path": RESULT_PATHS[-1]},
                    },
                    "body": malformed_body,
                },
            ),
            (
                "alb-post",
                dict(
                    httpMethod="POST",
                    path=RESULT_PATHS[-1],
                    requestContext={"elb": {"targetGroupArn": "proof-only"}},
                    body=malformed_body,
                ),
            ),
            (
                "rest-method-missing",
                {
                    "path": RESULT_PATHS[-1],
                    "requestContext": {
                        "apiId": "hostile-direct-proof",
                        "resourceId": "result-signals",
                    },
                    "body": malformed_body,
                },
            ),
        ]
    )
    return envelopes


def invoke_hostile_http_gate(lambdas: Any, *, function_name: str) -> List[Dict[str, Any]]:
    envelopes = hostile_http_envelopes()
    rows = []
    for label, event in envelopes:
        response = lambdas.invoke(
            FunctionName=function_name,
            InvocationType="RequestResponse",
            Payload=json.dumps(event, separators=(",", ":")).encode("utf-8"),
        )
        payload = _lambda_payload(response)
        status = payload.get("statusCode")
        body = _parse_json_object(payload.get("body"), label=f"{label} body")
        headers = {
            str(key).lower(): str(value)
            for key, value in (payload.get("headers") or {}).items()
        }
        if status != 405:
            raise VerificationError(f"Hostile envelope {label} returned {status}, not 405")
        if str(headers.get("allow") or "") != "GET,OPTIONS":
            raise VerificationError(f"Hostile envelope {label} lacks exact Allow header")
        if _cors_methods(headers) != ("GET", "OPTIONS"):
            raise VerificationError(f"Hostile envelope {label} advertises mutation")
        if body.get("error") != "HTTP mutation methods are disabled; use GET for read-only reports":
            raise VerificationError(f"Hostile envelope {label} returned an unexpected error")
        rows.append(
            {
                "envelope": label,
                "status": status,
                "method": body.get("method"),
                "path": body.get("path"),
                "gatePrecededMalformedDaysFromParsing": True,
            }
        )
    return rows


def verify_bounded_probe_duration(
    *,
    probe_started: datetime,
    probe_finished: datetime,
    probe_budget_seconds: int,
) -> Dict[str, Any]:
    if probe_budget_seconds <= 0:
        raise VerificationError("Probe budget must be positive")
    duration_seconds = (probe_finished - probe_started).total_seconds()
    if duration_seconds < 0 or duration_seconds > probe_budget_seconds:
        raise VerificationError(
            "HTTP/DynamoDB historical-partition probe exceeded its bounded budget: "
            f"{duration_seconds:.3f}s"
        )
    return {
        "probeStartedAtUtc": _iso(probe_started),
        "probeFinishedAtUtc": _iso(probe_finished),
        "durationSeconds": round(duration_seconds, 3),
        "withinBudget": True,
        "scheduleGapOrWriterQuiescenceAsserted": False,
    }


def _new_summary_rows(
    before: Iterable[Mapping[str, Any]],
    after: Iterable[Mapping[str, Any]],
    *,
    not_before: datetime,
) -> List[Dict[str, Any]]:
    before_keys = {
        (str(item.get("PK") or ""), str(item.get("SK") or ""))
        for item in before
    }
    rows = []
    for raw in after:
        item = dict(raw)
        key = (str(item.get("PK") or ""), str(item.get("SK") or ""))
        if key in before_keys:
            continue
        if item.get("entity_type") != "MLB_RESULT_SIGNAL_LEARNING_SUMMARY":
            continue
        created_raw = str(item.get("created_at") or "")
        try:
            created = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if created.astimezone(timezone.utc) < not_before.astimezone(timezone.utc):
            continue
        rows.append(item)
    return sorted(rows, key=lambda row: str(row.get("SK") or ""))


def wait_for_natural_schedule_advance(
    table: Any,
    *,
    baseline: Sequence[Mapping[str, Any]],
    slate_date_et: str,
    timeout_seconds: int,
    observation_start: datetime,
    expected_rule_arn: str,
) -> Dict[str, Any]:
    pk = f"RESULT_SIGNAL#mlb#{slate_date_et}"
    started = observation_start
    deadline = time.monotonic() + timeout_seconds
    while True:
        current = _bounded_query(table, pk)
        new_summaries = _new_summary_rows(
            baseline,
            current,
            not_before=started,
        )
        native_summaries: List[Tuple[Dict[str, Any], Dict[str, str]]] = []
        other_occurrence_summaries: List[Dict[str, str]] = []
        for candidate in new_summaries:
            raw_provenance = candidate.get("producer_provenance")
            if raw_provenance is None:
                continue
            provenance = _validated_native_schedule_provenance(
                raw_provenance,
                expected_rule_arn=expected_rule_arn,
                window_start=started,
                enforce_selected_occurrence=False,
            )
            event_time = datetime.fromisoformat(
                provenance["event_time_utc"].replace("Z", "+00:00")
            )
            if not started <= event_time < started + timedelta(minutes=15):
                # A prior 600-second invocation may finish after this fence.
                # Native event time makes it unambiguously unrelated, so keep
                # observing for the selected occurrence instead of false-red.
                other_occurrence_summaries.append(
                    {
                        "PK": str(candidate.get("PK") or ""),
                        "SK": str(candidate.get("SK") or ""),
                        "event_time_utc": provenance["event_time_utc"],
                    }
                )
                continue
            native_summaries.append((candidate, provenance))
        if native_summaries:
            if len(native_summaries) != 1:
                raise VerificationError(
                    "Natural schedule window produced more than one native-provenance summary"
                )
            row, provenance = native_summaries[0]
            if row.get("PK") != pk:
                raise VerificationError("Scheduled summary has the wrong partition")
            if row.get("sport") != "mlb" or row.get("game_date_et") != slate_date_et:
                raise VerificationError("Scheduled summary has the wrong sport/slate binding")
            if row.get("version") != "MLB-RESULT-SIGNAL-LEARNING-v1":
                raise VerificationError("Scheduled summary has an unexpected schema version")
            if not isinstance(row.get("summary"), dict):
                raise VerificationError("Scheduled summary payload is not an object")
            if int(row.get("stored_rows") or 0) < 0:
                raise VerificationError("Scheduled summary stored_rows is negative")
            created = datetime.fromisoformat(
                str(row.get("created_at") or "").replace("Z", "+00:00")
            )
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            sk_raw = str(row.get("SK") or "")
            if not sk_raw.startswith("SUMMARY#"):
                raise VerificationError("Scheduled summary key lacks SUMMARY prefix")
            try:
                key_time = datetime.fromisoformat(
                    sk_raw.removeprefix("SUMMARY#").replace("Z", "+00:00")
                )
            except ValueError as exc:
                raise VerificationError("Scheduled summary key timestamp is invalid") from exc
            if key_time.tzinfo is None:
                key_time = key_time.replace(tzinfo=timezone.utc)
            if abs((created - key_time).total_seconds()) > 2:
                raise VerificationError("Scheduled summary key/created_at timestamps diverge")
            observed = _now()
            if created < started or created > observed + timedelta(seconds=5):
                raise VerificationError("Scheduled summary timestamp is outside observation bounds")
            event_time = datetime.fromisoformat(
                provenance["event_time_utc"].replace("Z", "+00:00")
            )
            if created < event_time:
                raise VerificationError("Scheduled summary predates its native EventBridge event")
            return {
                "partition": pk,
                "waitStartedAtUtc": _iso(started),
                "observedAtUtc": _iso(observed),
                "baselineCount": len(baseline),
                "finalCount": len(current),
                "newPartitionSummaryCount": len(new_summaries),
                "ignoredUnprovenancedSummaryCount": (
                    len(new_summaries)
                    - len(native_summaries)
                    - len(other_occurrence_summaries)
                ),
                "ignoredOtherOccurrenceSummaryCount": len(
                    other_occurrence_summaries
                ),
                "ignoredOtherOccurrenceSummaries": other_occurrence_summaries,
                "newSummaryCount": len(native_summaries),
                "newSummaryKey": {
                    "PK": row.get("PK"),
                    "SK": row.get("SK"),
                },
                "newSummaryCreatedAt": row.get("created_at"),
                "newSummaryFingerprint": _sha256(row),
                "producerProvenance": provenance,
                "naturalScheduleWindowAdvanceObserved": True,
                "causalBinding": (
                    "NATIVE_EVENTBRIDGE_ENVELOPE_AND_LAMBDA_REQUEST_ID"
                ),
            }
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise VerificationError(
                "No new scheduled result-signal summary appeared before timeout"
            )
        time.sleep(min(20, remaining))


def _next_natural_schedule_boundary(after: datetime) -> datetime:
    """Return the first cron(6/15) minute bucket strictly after ``after``."""

    value = after.astimezone(timezone.utc)
    candidate = value.replace(second=0, microsecond=0) + timedelta(minutes=1)
    while candidate.minute % 15 != 6:
        candidate += timedelta(minutes=1)
    return candidate


def _slate_date_et_at(value: datetime) -> str:
    return value.astimezone(EASTERN).date().isoformat()


def _validated_native_schedule_provenance(
    raw: Any,
    *,
    expected_rule_arn: str,
    window_start: datetime,
    enforce_selected_occurrence: bool = True,
) -> Dict[str, str]:
    required = {
        "schema_version",
        "authority",
        "lambda_request_id",
        "event_id",
        "event_time_utc",
        "event_source",
        "detail_type",
        "rule_arn",
        "account",
        "region",
    }
    if not isinstance(raw, Mapping) or set(raw) != required:
        raise VerificationError("Scheduled summary producer provenance shape mismatch")
    value = {key: str(raw.get(key) or "").strip() for key in required}
    if not all(value.values()):
        raise VerificationError("Scheduled summary producer provenance has an empty field")
    if value["schema_version"] != RESULT_SIGNAL_PRODUCER_PROOF_VERSION:
        raise VerificationError("Scheduled summary producer provenance version mismatch")
    if value["authority"] != RESULT_SIGNAL_PRODUCER_AUTHORITY:
        raise VerificationError("Scheduled summary producer authority mismatch")
    if value["event_source"] != "aws.events" or value["detail_type"] != "Scheduled Event":
        raise VerificationError("Scheduled summary lacks native EventBridge identity")
    if value["rule_arn"] != expected_rule_arn:
        raise VerificationError("Scheduled summary rule ARN mismatch")
    try:
        uuid.UUID(value["lambda_request_id"])
        uuid.UUID(value["event_id"])
    except ValueError as exc:
        raise VerificationError("Scheduled summary request/event ID is invalid") from exc

    arn_parts = expected_rule_arn.split(":", 5)
    if (
        len(arn_parts) != 6
        or arn_parts[2] != "events"
        or not arn_parts[5].startswith("rule/")
        or value["region"] != arn_parts[3]
        or value["account"] != arn_parts[4]
    ):
        raise VerificationError("Scheduled summary rule/account/region binding mismatch")
    try:
        event_time = datetime.fromisoformat(
            value["event_time_utc"].replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise VerificationError("Scheduled summary EventBridge time is invalid") from exc
    if event_time.tzinfo is None:
        raise VerificationError("Scheduled summary EventBridge time lacks timezone")
    event_time = event_time.astimezone(timezone.utc)
    occurrence_start = window_start.astimezone(timezone.utc).replace(
        second=0,
        microsecond=0,
    )
    next_occurrence = occurrence_start + timedelta(minutes=15)
    if (
        enforce_selected_occurrence
        and not occurrence_start <= event_time < next_occurrence
    ):
        raise VerificationError(
            "Scheduled summary EventBridge time misses selected cron occurrence"
        )
    value["event_time_utc"] = _iso(event_time)
    return value


def prepare_post_probe_schedule_observation(
    table: Any,
    *,
    probe_completed_at: datetime,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Capture a pre-fence baseline, retrying if its query crosses the fence.

    The selected cron minute is strictly later than the completed HTTP probes.
    A strongly consistent, paginated query can be slow, so no candidate is used
    unless that query finishes strictly before its boundary.
    """

    reference = probe_completed_at
    for attempt in range(1, 9):
        candidate_from = max(reference, _now())
        window_start = _next_natural_schedule_boundary(candidate_from)
        midnight_sensitive_candidates: List[str] = []
        while _slate_date_et_at(window_start) != _slate_date_et_at(
            window_start + SCHEDULE_TO_HANDLER_MAX_AGE
        ):
            # EventBridge and Lambda async delivery each allow five minutes of
            # event age.  Do not watch a partition whose execution-date slate
            # could legitimately change while that occurrence is in flight.
            midnight_sensitive_candidates.append(_iso(window_start))
            window_start = _next_natural_schedule_boundary(window_start)
        slate_date_et = _slate_date_et_at(window_start)
        partition = f"RESULT_SIGNAL#mlb#{slate_date_et}"
        baseline_started = _now()
        baseline = _bounded_query(table, partition)
        baseline_completed = _now()
        if baseline_completed >= window_start:
            reference = baseline_completed
            continue
        while True:
            now = _now()
            remaining = (window_start - now).total_seconds()
            if remaining <= 0:
                return (
                    {
                        "probeCompletedAtUtc": _iso(probe_completed_at),
                        "windowStartUtc": _iso(window_start),
                        "openedAtUtc": _iso(now),
                        "scheduledSlateDateEt": slate_date_et,
                        "partition": partition,
                        "baselineStartedAtUtc": _iso(baseline_started),
                        "baselineCompletedAtUtc": _iso(baseline_completed),
                        "baselineCount": len(baseline),
                        "baselineFingerprint": _sha256(baseline),
                        "baselineAttempt": attempt,
                        "baselineCompletedStrictlyBeforeFence": True,
                        "maximumScheduleToHandlerAgeSeconds": int(
                            SCHEDULE_TO_HANDLER_MAX_AGE.total_seconds()
                        ),
                        "midnightSensitiveCandidatesSkipped": (
                            midnight_sensitive_candidates
                        ),
                        "candidateAvoidsEtMidnightDeliveryHorizon": True,
                        "probeMinuteBucketExcluded": True,
                        "opensOnExpectedNaturalScheduleMinute": True,
                        "httpMutationAttributionAsserted": False,
                    },
                    baseline,
                )
            time.sleep(min(20, remaining))
    raise VerificationError(
        "Strongly consistent result-summary baseline crossed eight cron fences"
    )


def _metric_sum(
    cloudwatch: Any,
    *,
    namespace: str,
    metric_name: str,
    dimensions: Sequence[Mapping[str, str]],
    start: datetime,
    end: datetime,
    statistic: str = "Sum",
) -> float:
    response = cloudwatch.get_metric_statistics(
        Namespace=namespace,
        MetricName=metric_name,
        Dimensions=list(dimensions),
        StartTime=start,
        EndTime=end,
        Period=60,
        Statistics=[statistic],
    )
    return float(
        sum(
            float(row.get(statistic) or 0)
            for row in response.get("Datapoints") or []
        )
    )


def wait_for_schedule_metrics(
    cloudwatch: Any,
    *,
    function_name: str,
    rule_name: str,
    start: datetime,
    timeout_seconds: int = 5 * 60,
    publication_settle_seconds: int = 120,
) -> Dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    visible_at: Optional[float] = None
    while True:
        metric_start = start
        # CloudWatch EndTime is exclusive.  Keep the occurrence fixed even if
        # the 600-second producer or metric publication polling crosses the
        # next healthy cron tick.
        end = start + timedelta(minutes=15)
        lambda_invocations = _metric_sum(
            cloudwatch,
            namespace="AWS/Lambda",
            metric_name="Invocations",
            dimensions=[{"Name": "FunctionName", "Value": function_name}],
            start=metric_start,
            end=end,
        )
        lambda_errors = _metric_sum(
            cloudwatch,
            namespace="AWS/Lambda",
            metric_name="Errors",
            dimensions=[{"Name": "FunctionName", "Value": function_name}],
            start=metric_start,
            end=end,
        )
        rule_invocations = _metric_sum(
            cloudwatch,
            namespace="AWS/Events",
            metric_name="Invocations",
            dimensions=[{"Name": "RuleName", "Value": rule_name}],
            start=metric_start,
            end=end,
        )
        rule_failures = _metric_sum(
            cloudwatch,
            namespace="AWS/Events",
            metric_name="FailedInvocations",
            dimensions=[{"Name": "RuleName", "Value": rule_name}],
            start=metric_start,
            end=end,
        )
        if rule_failures > 0:
            raise VerificationError(
                "Scheduled EventBridge rule emitted delivery failures: "
                f"ruleFailures={rule_failures}"
            )
        if rule_invocations > 1:
            raise VerificationError(
                "Scheduled EventBridge delivery window is ambiguous: "
                f"ruleInvocations={rule_invocations}"
            )
        if rule_invocations == 1 and visible_at is None:
            visible_at = time.monotonic()
        settled = bool(
            visible_at is not None
            and time.monotonic() - visible_at >= publication_settle_seconds
        )
        if settled:
            return {
                "windowStartUtc": _iso(metric_start),
                "windowEndUtc": _iso(end),
                "lambdaInvocations": lambda_invocations,
                "lambdaErrors": lambda_errors,
                "eventBridgeInvocations": rule_invocations,
                "eventBridgeFailedInvocations": rule_failures,
                "publicationSettleSeconds": publication_settle_seconds,
                "eventBridgeDeliveryAuthoritative": True,
                "lambdaAggregateMetricsAuthoritative": False,
                "lambdaAggregateMetricsDiagnosticReason": (
                    "The same function serves public GET/OPTIONS concurrently; "
                    "the persisted summary is bound by Lambda request ID instead."
                ),
                "correlation": (
                    "ONE_RULE_DELIVERY_SUPPORTS_REQUEST_BOUND_NATIVE_SUMMARY"
                ),
                "clean": True,
                "cleanScope": "EVENTBRIDGE_DELIVERY_ONLY",
            }
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise VerificationError(
                "CloudWatch did not expose one settled EventBridge delivery metric"
            )
        time.sleep(min(20, remaining))


def _log_events(
    logs: Any,
    *,
    log_group: str,
    start: datetime,
    end: datetime,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    token: Optional[str] = None
    seen = set()
    for _ in range(100):
        kwargs: Dict[str, Any] = {
            "logGroupName": log_group,
            "startTime": int(start.timestamp() * 1000),
            "endTime": int(end.timestamp() * 1000),
            "limit": 10_000,
        }
        if token:
            kwargs["nextToken"] = token
        response = logs.filter_log_events(**kwargs)
        rows.extend(response.get("events") or [])
        next_token = response.get("nextToken")
        if not next_token or next_token == token:
            return rows
        if next_token in seen:
            raise VerificationError("CloudWatch Logs pagination cursor cycled")
        seen.add(next_token)
        token = str(next_token)
    raise VerificationError("CloudWatch Logs pagination exceeded 100 pages")


def wait_for_request_bound_clean_lambda_log(
    logs: Any,
    *,
    function_name: str,
    start: datetime,
    end: datetime,
    request_id: str,
    event_id: str,
    rule_arn: str,
    summary_key: Mapping[str, Any],
    timeout_seconds: int = 5 * 60,
) -> Dict[str, Any]:
    log_group = f"/aws/lambda/{function_name}"
    deadline = time.monotonic() + timeout_seconds
    start_pattern = re.compile(r"^START RequestId: ([0-9a-f-]+) Version:")
    end_pattern = re.compile(r"^END RequestId: ([0-9a-f-]+)")
    report_pattern = re.compile(r"^REPORT RequestId: ([0-9a-f-]+)")
    while True:
        events = _log_events(
            logs,
            log_group=log_group,
            start=start,
            end=end + timedelta(minutes=2),
        )
        messages = [str(event.get("message") or "").strip() for event in events]
        starts = [
            match.group(1)
            for message in messages
            if (match := start_pattern.match(message))
        ]
        ends = [
            match.group(1)
            for message in messages
            if (match := end_pattern.match(message))
        ]
        reports = [
            match.group(1)
            for message in messages
            if (match := report_pattern.match(message))
        ]
        target_starts = [value for value in starts if value == request_id]
        target_ends = [value for value in ends if value == request_id]
        target_reports = [value for value in reports if value == request_id]
        structured_rows = []
        for message in messages:
            try:
                parsed = json.loads(message)
            except (TypeError, ValueError):
                continue
            if (
                isinstance(parsed, dict)
                and parsed.get("event") == "MLB_RESULT_SIGNAL_SUMMARY_PERSISTED"
                and parsed.get("lambda_request_id") == request_id
            ):
                structured_rows.append(parsed)
        if (
            len(target_starts) > 1
            or len(target_ends) > 1
            or len(target_reports) > 1
            or len(structured_rows) > 1
        ):
            raise VerificationError("Request-bound scheduled producer log is duplicated")
        if (
            len(target_starts)
            == len(target_ends)
            == len(target_reports)
            == len(structured_rows)
            == 1
        ):
            structured = structured_rows[0]
            expected_structured = {
                "event": "MLB_RESULT_SIGNAL_SUMMARY_PERSISTED",
                "schema_version": RESULT_SIGNAL_PRODUCER_PROOF_VERSION,
                "lambda_request_id": request_id,
                "event_id": event_id,
                "rule_arn": rule_arn,
                "PK": summary_key.get("PK"),
                "SK": summary_key.get("SK"),
            }
            if structured != expected_structured:
                raise VerificationError(
                    "Request-bound scheduled producer structured log mismatch"
                )
            report_message = next(
                message
                for message in messages
                if (
                    (match := report_pattern.match(message))
                    and match.group(1) == request_id
                )
            )
            report_status_match = re.search(
                r"(?:^|\s)Status:\s*([^\s]+)",
                report_message,
                flags=re.IGNORECASE,
            )
            report_status = (
                report_status_match.group(1).strip().lower()
                if report_status_match
                else None
            )
            if (
                (report_status is not None and report_status != "success")
                or "Error Type:" in report_message
            ):
                raise VerificationError(
                    "Scheduled producer REPORT log has a non-success status"
                )
            all_request_ids = sorted(set(starts) | set(ends) | set(reports))
            unrelated = [value for value in all_request_ids if value != request_id]
            return {
                "logGroup": log_group,
                "requestId": request_id,
                "startCount": 1,
                "endCount": 1,
                "reportCount": 1,
                "structuredPersistenceLogCount": 1,
                "structuredPersistenceLogSha256": hashlib.sha256(
                    _canonical_json(structured).encode("utf-8")
                ).hexdigest(),
                "unrelatedRequestIds": unrelated,
                "unrelatedRequestCount": len(unrelated),
                "unrelatedRequestsGateVerification": False,
                "reportSha256": hashlib.sha256(
                    report_message.encode("utf-8")
                ).hexdigest(),
                "reportStatus": report_status or "NOT_EMITTED_BY_PLATFORM",
                "clean": True,
            }
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise VerificationError(
                "CloudWatch Logs did not expose the request-bound scheduled invocation"
            )
        time.sleep(min(20, remaining))


def verify_ml_training_protected_partition_isolation(
    *,
    source_overrides: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    """Prove the :04 capture/full trainer writes only its Snapshots store."""

    trainer_paths = (
        Path("hello_world/mlb_ml_aws_training_v1.py"),
        Path("hello_world/mlb_ml_aws_training_v1_compat.py"),
        Path("hello_world/mlb_prospective_trainer_read_repair.py"),
        Path("hello_world/mlb_r7_source_honest_training_repair.py"),
    )
    overrides = dict(source_overrides or {})
    sources = {
        str(path): overrides.get(str(path), path.read_text(encoding="utf-8"))
        for path in trainer_paths
    }
    canonical_path = str(trainer_paths[0])
    canonical_source = sources[canonical_path]
    required_anchors = (
        'self.table = dynamodb_resource.Table(table_name)',
        'table_name=os.environ.get("SNAPSHOTS_TABLE", "")',
        'labels.load_canonical_locked_rows_without_labels(',
    )
    missing = [anchor for anchor in required_anchors if anchor not in canonical_source]
    if missing:
        raise VerificationError(
            "ML trainer table/source isolation anchors changed: "
            + _canonical_json(missing)
        )
    protected_prefixes = (
        "OUTCOME#mlb#",
        "MLB_CANONICAL_FINAL_LABEL#",
        "PRED#mlb#",
        "RESULT_SIGNAL#mlb#",
    )
    prefix_hits = {
        path: [prefix for prefix in protected_prefixes if prefix in source]
        for path, source in sources.items()
    }
    prefix_hits = {path: hits for path, hits in prefix_hits.items() if hits}
    if prefix_hits:
        raise VerificationError(
            "ML trainer contains a protected partition-key writer literal: "
            + _canonical_json(prefix_hits)
        )

    canonical_tree = ast.parse(canonical_source, filename=canonical_path)
    store_class = next(
        (
            node
            for node in canonical_tree.body
            if isinstance(node, ast.ClassDef) and node.name == "AwsTrainingStore"
        ),
        None,
    )
    if store_class is None:
        raise VerificationError("AwsTrainingStore source class is missing")
    mutation_methods = {
        "put_item",
        "update_item",
        "delete_item",
        "transact_write_items",
        "batch_write_item",
        "batch_writer",
        "execute_statement",
        "batch_execute_statement",
        "transact_execute_statement",
    }
    mutation_receivers = []
    for node in ast.walk(store_class):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr in mutation_methods:
            mutation_receivers.append(ast.unparse(node.func.value))
    allowed_receivers = {"self.table", "self.table.meta.client", "client"}
    if not mutation_receivers or any(
        receiver not in allowed_receivers for receiver in mutation_receivers
    ):
        raise VerificationError(
            "ML trainer DDB mutation receiver escaped AwsTrainingStore: "
            + _canonical_json(mutation_receivers)
        )
    client_assignments = [
        node
        for node in ast.walk(store_class)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "client"
            for target in node.targets
        )
    ]
    if any(
        ast.unparse(node.value) != "self.table.meta.client"
        for node in client_assignments
    ):
        raise VerificationError("ML trainer local DDB client is not store-table-bound")

    # Fail closed if a recognized DynamoDB mutation is ever moved into a
    # compatibility/repair module or outside the one source-proven store class.
    store_node_ids = {id(node) for node in ast.walk(store_class)}
    escaped_mutations: Dict[str, List[Dict[str, Any]]] = {}
    source_trees = {
        path: canonical_tree
        if path == canonical_path
        else ast.parse(source, filename=path)
        for path, source in sources.items()
    }
    for path, tree in source_trees.items():
        for node in ast.walk(tree):
            if (
                not isinstance(node, ast.Call)
                or not isinstance(node.func, ast.Attribute)
                or node.func.attr not in mutation_methods
            ):
                continue
            if path == canonical_path and id(node) in store_node_ids:
                continue
            escaped_mutations.setdefault(path, []).append(
                {
                    "line": int(getattr(node, "lineno", 0)),
                    "method": node.func.attr,
                    "receiver": ast.unparse(node.func.value),
                }
            )
    if escaped_mutations:
        raise VerificationError(
            "ML trainer DDB mutation escaped AwsTrainingStore: "
            + _canonical_json(escaped_mutations)
        )

    labels_path = Path("hello_world/mlb_canonical_final_labels_v1.py")
    labels_source = labels_path.read_text(encoding="utf-8")
    labels_tree = ast.parse(labels_source, filename=str(labels_path))
    read_only_functions = {
        "load_canonical_locked_rows_without_labels",
        "load_canonical_training_rows",
        "_validated_canonical_locks",
        "_labels_for_slate",
        "_query_partition",
    }
    found = set()
    mutation_calls: List[str] = []
    for node in labels_tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name not in read_only_functions:
            continue
        found.add(node.name)
        for child in ast.walk(node):
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr in mutation_methods
            ):
                mutation_calls.append(f"{node.name}:{child.func.attr}")
    if found != read_only_functions or mutation_calls:
        raise VerificationError(
            "ML trainer canonical-label readers are not source-proven read-only: "
            + _canonical_json(
                {
                    "missing": sorted(read_only_functions - found),
                    "mutationCalls": mutation_calls,
                }
            )
        )
    return {
        "selectionCaptureSchedule": "cron(4/15 * * * ? *)",
        "fullTrainingSchedule": "cron(11 1/6 * * ? *)",
        "writerStore": "AwsTrainingStore",
        "writerTableEnvironment": "SNAPSHOTS_TABLE",
        "outcomesAccess": "CANONICAL_LABEL_READ_ONLY",
        "allDynamoDbMutationCallsConfinedToAwsTrainingStore": True,
        "protectedPartitionPrefixes": list(protected_prefixes),
        "canonicalLabelReaderMutationCalls": [],
        "protectedOutcomesPredictionsResultSignalsOrLabelsWritable": False,
        "sourceSha256": {
            **{
                path: hashlib.sha256(source.encode("utf-8")).hexdigest()
                for path, source in sources.items()
            },
            str(labels_path): hashlib.sha256(
                labels_source.encode("utf-8")
            ).hexdigest(),
        },
    }


def run(args: argparse.Namespace, evidence: Dict[str, Any]) -> None:
    import boto3

    session = boto3.session.Session(region_name=args.region)
    cfn = session.client("cloudformation")
    apigateway = session.client("apigateway")
    lambdas = session.client("lambda")
    events = session.client("events")
    cloudwatch = session.client("cloudwatch")
    logs = session.client("logs")
    dynamodb = session.resource("dynamodb")

    function_name = _resource_physical_id(cfn, args.stack_name, RESULTS_LOGICAL_ID)
    function = lambdas.get_function(FunctionName=function_name)
    config = function.get("Configuration") or {}
    async_config = lambdas.get_function_event_invoke_config(FunctionName=function_name)
    expected_async_config = {
        "maximumEventAgeInSeconds": 300,
        "maximumRetryAttempts": 0,
        "destinationConfig": {},
    }
    actual_async_config = {
        "maximumEventAgeInSeconds": async_config.get("MaximumEventAgeInSeconds"),
        "maximumRetryAttempts": async_config.get("MaximumRetryAttempts"),
        "destinationConfig": async_config.get("DestinationConfig") or {},
    }
    if actual_async_config != expected_async_config:
        raise VerificationError(
            f"Results Lambda async invoke policy mismatch: {actual_async_config}"
        )
    function_arn = str(config.get("FunctionArn") or "")
    deploy_sha = str(
        ((config.get("Environment") or {}).get("Variables") or {}).get(
            "INQSI_DEPLOY_GIT_SHA"
        )
        or ""
    )
    if deploy_sha != args.expected_deploy_sha:
        raise VerificationError(
            f"Deployed results Lambda SHA mismatch: expected={args.expected_deploy_sha} actual={deploy_sha}"
        )
    environment = (config.get("Environment") or {}).get("Variables") or {}
    deploy_run_id = str(environment.get("INQSI_DEPLOY_RUN_ID") or "")
    if deploy_run_id != args.expected_deploy_run_id:
        raise VerificationError(
            "Deployed results Lambda run identity mismatch: "
            f"expected={args.expected_deploy_run_id} actual={deploy_run_id}"
        )
    template_sha256 = hashlib.sha256(
        Path("template.yaml").read_bytes()
    ).hexdigest()
    deployed_template_sha256 = str(
        environment.get("INQSI_DEPLOY_TEMPLATE_SHA256") or ""
    )
    if deployed_template_sha256 != template_sha256:
        raise VerificationError(
            "Deployed results Lambda template identity mismatch: "
            f"expected={template_sha256} actual={deployed_template_sha256}"
        )
    if config.get("Handler") != EXPECTED_HANDLER:
        raise VerificationError(f"Results Lambda handler mismatch: {config.get('Handler')}")
    if (
        config.get("Runtime") != "python3.11"
        or config.get("Timeout") != 600
        or config.get("MemorySize") != 2048
    ):
        raise VerificationError(
            "Results Lambda runtime sizing mismatch: "
            f"runtime={config.get('Runtime')} timeout={config.get('Timeout')} "
            f"memory={config.get('MemorySize')}"
        )
    if config.get("State") != "Active" or config.get("LastUpdateStatus") != "Successful":
        raise VerificationError(
            "Results Lambda is not stable: "
            f"state={config.get('State')} update={config.get('LastUpdateStatus')}"
        )
    evidence["deployedFunction"] = {
        "logicalId": RESULTS_LOGICAL_ID,
        "functionName": function_name,
        "functionArn": function_arn,
        "handler": config.get("Handler"),
        "runtime": config.get("Runtime"),
        "timeoutSeconds": config.get("Timeout"),
        "memorySizeMb": config.get("MemorySize"),
        "roleArn": config.get("Role"),
        "architectures": config.get("Architectures"),
        "packageType": config.get("PackageType"),
        "codeSha256": config.get("CodeSha256"),
        "revisionId": config.get("RevisionId"),
        "lastModified": config.get("LastModified"),
        "deployGitSha": deploy_sha,
        "deployRunId": deploy_run_id,
        "templateSha256": deployed_template_sha256,
        "state": config.get("State"),
        "lastUpdateStatus": config.get("LastUpdateStatus"),
        "asyncInvokeConfig": actual_async_config,
    }
    expected_build_manifest = _parse_json_object(
        Path(args.deploy_build_manifest).read_bytes(),
        label="exact deploy build manifest",
    )
    deploy_identity = _parse_json_object(
        Path(args.deploy_identity).read_bytes(),
        label="exact deploy identity proof",
    )
    evidence["deployedCodeArtifactBinding"] = verify_deployed_code_artifact(
        function,
        expected_build_manifest=expected_build_manifest,
        deploy_identity=deploy_identity,
        expected_deploy_sha=args.expected_deploy_sha,
        expected_template_sha256=template_sha256,
        expected_deploy_run_id=args.expected_deploy_run_id,
    )

    rest_api_id = _resource_physical_id(cfn, args.stack_name, API_LOGICAL_ID)
    api_url = _stack_output(cfn, args.stack_name, "ApiUrl")
    evidence["apiSurface"] = verify_api_surface(
        apigateway,
        rest_api_id=rest_api_id,
        function_arn=function_arn,
    )
    evidence["apiSurface"]["restApiId"] = rest_api_id
    evidence["apiUrlBinding"] = verify_api_url(
        api_url,
        rest_api_id=rest_api_id,
        region=args.region,
        stage_name="Prod",
    )
    evidence["deployedStage"] = verify_deployed_stage(
        apigateway,
        rest_api_id=rest_api_id,
        function_arn=function_arn,
        stage_name="Prod",
    )
    evidence["schedule"] = verify_schedule(events, function_arn=function_arn)

    table_names = {
        logical_id: _resource_physical_id(cfn, args.stack_name, logical_id)
        for logical_id in (
            "SnapshotsTable",
            "OutcomesTable",
            "PredictionsTable",
            "SignalLedgerTable",
        )
    }
    expected_table_environment = {
        "SNAPSHOTS_TABLE": table_names["SnapshotsTable"],
        "OUTCOMES_TABLE": table_names["OutcomesTable"],
        "PREDICTIONS_TABLE": table_names["PredictionsTable"],
        "SIGNAL_LEDGER_TABLE": table_names["SignalLedgerTable"],
    }
    actual_table_environment = {
        key: environment.get(key)
        for key in expected_table_environment
    }
    if actual_table_environment != expected_table_environment:
        raise VerificationError(
            "Results Lambda table environment is not bound to snapshotted tables: "
            f"expected={expected_table_environment} actual={actual_table_environment}"
        )
    evidence["deployedFunction"]["tableEnvironment"] = actual_table_environment
    tables = {
        logical_id: dynamodb.Table(table_name)
        for logical_id, table_name in table_names.items()
    }
    evidence["mlTrainingProtectedPartitionIsolation"] = (
        verify_ml_training_protected_partition_isolation()
    )
    diagnostic_reference_slate_et = _slate_date_et_at(_now())
    authoritative_partitions = authoritative_tracked_partitions(
        args.probe_slate_date
    )
    diagnostic_partitions = diagnostic_current_partitions(
        diagnostic_reference_slate_et
    )
    evidence["probeIsolation"] = {
        "strategy": "PINNED_HISTORICAL_PARTITIONS_OUTSIDE_RECURRING_WRITER_AUTHORITY",
        "pinnedSlateDateEt": args.probe_slate_date,
        "scheduleGapOrWriterQuiescenceAsserted": False,
        "currentRecentPartitionsAuthoritative": False,
    }
    probe_started = _now()
    before = snapshot_partitions(tables, authoritative_partitions)
    diagnostic_before = snapshot_partitions_diagnostic(
        tables, diagnostic_partitions
    )
    historical_baseline_count = sum(
        int(partition.get("count") or 0)
        for table in before.values()
        for partition in (table.get("partitions") or {}).values()
    )
    if historical_baseline_count < 1:
        raise VerificationError(
            "Reviewed historical probe slate has no baseline material in protected tables"
        )
    hostile = invoke_hostile_http_gate(lambdas, function_name=function_name)
    public = invoke_public_surface(
        api_url=api_url,
        probe_slate_date=args.probe_slate_date,
    )
    after = snapshot_partitions(tables, authoritative_partitions)
    diagnostic_after = snapshot_partitions_diagnostic(
        tables, diagnostic_partitions
    )
    probe_finished = _now()
    bounded_completion = verify_bounded_probe_duration(
        probe_started=probe_started,
        probe_finished=probe_finished,
        probe_budget_seconds=args.max_probe_seconds,
    )
    # Only the exact reviewed historical slate is authoritative. Recurring
    # producers operate on current/recent slates and cannot legitimately touch
    # these pinned keys. Current/recent canaries remain diagnostic-only because
    # their normal writes cannot be attributed to the HTTP requests.
    mutation_proof = verify_snapshots_unchanged(before, after)
    diagnostic_changes = partition_snapshot_changes(
        diagnostic_before, diagnostic_after
    )
    evidence["httpReadOnlyProof"] = {
        "startedAtUtc": _iso(probe_started),
        "completedAtUtc": _iso(probe_finished),
        "durationSeconds": round((probe_finished - probe_started).total_seconds(), 3),
        "probeSlateDateEt": args.probe_slate_date,
        "historicalBaselineItemCount": historical_baseline_count,
        "hostileDirectInvocations": hostile,
        "publicRequests": public,
        "before": before,
        "after": after,
        "hostileHttp405BeforePayloadDependencies": True,
        "boundedProbeCompletion": bounded_completion,
        "currentRecentPartitionCanaries": {
            "referenceSlateDateEt": diagnostic_reference_slate_et,
            "before": diagnostic_before,
            "after": diagnostic_after,
            "changes": diagnostic_changes,
            "changeCount": len(diagnostic_changes),
            "authoritative": False,
            "gatesVerification": False,
            "causalAttributionToHttp": False,
        },
        **mutation_proof,
    }

    # The HTTP mutation proof is complete.  Open a separate scheduling-only
    # observation on the next cron minute so the verifier's own synchronous
    # GET/OPTIONS/hostile probes remain outside Lambda's minute-bucket metrics
    # and platform-log window.
    schedule_fence, schedule_baseline = prepare_post_probe_schedule_observation(
        tables["SignalLedgerTable"],
        probe_completed_at=probe_finished,
    )
    schedule_window_start = datetime.fromisoformat(
        str(schedule_fence["windowStartUtc"]).replace("Z", "+00:00")
    )
    scheduled_slate_date_et = str(schedule_fence["scheduledSlateDateEt"])
    advance = wait_for_natural_schedule_advance(
        tables["SignalLedgerTable"],
        baseline=schedule_baseline,
        slate_date_et=scheduled_slate_date_et,
        timeout_seconds=args.schedule_wait_seconds,
        observation_start=schedule_window_start,
        expected_rule_arn=str(evidence["schedule"]["ruleArn"]),
    )
    advance["observationFence"] = schedule_fence
    evidence["scheduledAdvance"] = advance
    metric_start = datetime.fromisoformat(
        str(advance["waitStartedAtUtc"]).replace("Z", "+00:00")
    )
    evidence["scheduledAdvance"]["metrics"] = wait_for_schedule_metrics(
        cloudwatch,
        function_name=function_name,
        rule_name=evidence["schedule"]["ruleName"],
        start=metric_start,
    )
    metric_end = datetime.fromisoformat(
        str(advance["observedAtUtc"]).replace("Z", "+00:00")
    )
    producer_provenance = evidence["scheduledAdvance"]["producerProvenance"]
    evidence["scheduledAdvance"]["lambdaPlatformLog"] = (
        wait_for_request_bound_clean_lambda_log(
            logs,
            function_name=function_name,
            start=metric_start,
            end=metric_end,
            request_id=str(producer_provenance["lambda_request_id"]),
            event_id=str(producer_provenance["event_id"]),
            rule_arn=str(producer_provenance["rule_arn"]),
            summary_key=evidence["scheduledAdvance"]["newSummaryKey"],
        )
    )
    evidence["scheduledAdvance"]["binding"] = {
        "oneEnabledExactRuleTarget": True,
        "oneEventBridgeDelivery": True,
        "aggregateLambdaInvocationCountAuthoritative": False,
        "requestBoundLambdaRequestId": evidence["scheduledAdvance"]["lambdaPlatformLog"][
            "requestId"
        ],
        "nativeEventBridgeEventId": producer_provenance["event_id"],
        "nativeEventBridgeRuleArn": producer_provenance["rule_arn"],
        "oneNewValidatedSummary": True,
        "oneMatchingStructuredPersistenceLog": True,
        "bindingType": (
            "PRE_FENCE_KEY_DIFF_NATIVE_EVENTBRIDGE_REQUEST_ID_CAUSAL_BINDING"
        ),
    }
    evidence["scheduledAdvance"]["scope"] = {
        "asserts": (
            "NATURAL_METHODLESS_RESULTS_SCHEDULER_ADVANCEMENT_AND_PLATFORM_CLEANLINESS"
        ),
        "canonicalSettlementHttp200Asserted": False,
        "otherProducerHealthAsserted": False,
        "note": (
            "A methodless run may persist a result-signal summary and return an "
            "HTTP-style 409 without becoming a Lambda platform error."
        ),
    }

    # The proof spans at least one natural cadence. Re-read every authority
    # surface so a concurrent redeploy, stage change, rule edit, or table
    # replacement cannot combine unrelated before/after evidence.
    current_function = lambdas.get_function(FunctionName=function_name)
    current_config = current_function.get("Configuration") or {}
    current_environment = (current_config.get("Environment") or {}).get("Variables") or {}
    current_async_raw = lambdas.get_function_event_invoke_config(
        FunctionName=function_name
    )
    current_async_config = {
        "maximumEventAgeInSeconds": current_async_raw.get("MaximumEventAgeInSeconds"),
        "maximumRetryAttempts": current_async_raw.get("MaximumRetryAttempts"),
        "destinationConfig": current_async_raw.get("DestinationConfig") or {},
    }
    current_function_proof = {
        "logicalId": RESULTS_LOGICAL_ID,
        "functionName": function_name,
        "functionArn": current_config.get("FunctionArn"),
        "handler": current_config.get("Handler"),
        "runtime": current_config.get("Runtime"),
        "timeoutSeconds": current_config.get("Timeout"),
        "memorySizeMb": current_config.get("MemorySize"),
        "roleArn": current_config.get("Role"),
        "architectures": current_config.get("Architectures"),
        "packageType": current_config.get("PackageType"),
        "codeSha256": current_config.get("CodeSha256"),
        "revisionId": current_config.get("RevisionId"),
        "lastModified": current_config.get("LastModified"),
        "deployGitSha": current_environment.get("INQSI_DEPLOY_GIT_SHA"),
        "deployRunId": current_environment.get("INQSI_DEPLOY_RUN_ID"),
        "templateSha256": current_environment.get("INQSI_DEPLOY_TEMPLATE_SHA256"),
        "state": current_config.get("State"),
        "lastUpdateStatus": current_config.get("LastUpdateStatus"),
        "asyncInvokeConfig": current_async_config,
        "tableEnvironment": {
            key: current_environment.get(key)
            for key in expected_table_environment
        },
    }
    if current_function_proof != evidence["deployedFunction"]:
        raise VerificationError("Results Lambda identity/configuration changed during proof")
    current_resource_ids = {
        logical_id: _resource_physical_id(cfn, args.stack_name, logical_id)
        for logical_id in table_names
    }
    if current_resource_ids != table_names:
        raise VerificationError("A DynamoDB physical resource changed during proof")
    current_api_id = _resource_physical_id(cfn, args.stack_name, API_LOGICAL_ID)
    current_api_url = _stack_output(cfn, args.stack_name, "ApiUrl")
    if current_api_id != rest_api_id or current_api_url != api_url:
        raise VerificationError("API Gateway identity changed during proof")
    current_api_surface = verify_api_surface(
        apigateway,
        rest_api_id=rest_api_id,
        function_arn=function_arn,
    )
    current_api_surface["restApiId"] = rest_api_id
    if current_api_surface != evidence["apiSurface"]:
        raise VerificationError("API Gateway resource surface changed during proof")
    current_stage = verify_deployed_stage(
        apigateway,
        rest_api_id=rest_api_id,
        function_arn=function_arn,
        stage_name="Prod",
    )
    if current_stage != evidence["deployedStage"]:
        raise VerificationError("Deployed Prod-stage API export changed during proof")
    current_schedule = verify_schedule(events, function_arn=function_arn)
    if current_schedule != evidence["schedule"]:
        raise VerificationError("Results scheduler rule/target changed during proof")
    evidence["controlPlaneRecheck"] = {
        "verifiedAtUtc": _iso(_now()),
        "functionIdentityUnchanged": True,
        "tablePhysicalIdsUnchanged": True,
        "apiIdentityUnchanged": True,
        "apiResourceSurfaceUnchanged": True,
        "deployedStageExportUnchanged": True,
        "scheduleRuleAndTargetUnchanged": True,
        "stackStatus": "UPDATE_COMPLETE",
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify the deployed MLB results API is read-only while its natural scheduler advances."
    )
    parser.add_argument("--stack-name", default="parlay-platform-dev")
    parser.add_argument("--region", required=True)
    parser.add_argument("--expected-deploy-sha", required=True)
    parser.add_argument("--expected-deploy-run-id", required=True)
    parser.add_argument("--probe-slate-date", required=True)
    parser.add_argument("--deploy-lineage", required=True)
    parser.add_argument("--deploy-build-manifest", required=True)
    parser.add_argument("--deploy-identity", required=True)
    parser.add_argument("--verifier-workflow-sha", required=True)
    parser.add_argument("--verifier-run-id", required=True)
    parser.add_argument("--verifier-run-attempt", required=True)
    parser.add_argument("--schedule-wait-seconds", type=int, default=22 * 60)
    parser.add_argument("--max-probe-seconds", type=int, default=150)
    parser.add_argument(
        "--output",
        default="runtime_reports/mlb_results_api_postdeploy_latest.json",
    )
    args = parser.parse_args()

    if len(args.expected_deploy_sha) != 40 or any(
        char not in "0123456789abcdef" for char in args.expected_deploy_sha.lower()
    ):
        raise SystemExit("--expected-deploy-sha must be an exact 40-character Git SHA")
    try:
        date.fromisoformat(args.probe_slate_date)
    except ValueError as exc:
        raise SystemExit("--probe-slate-date must be ISO YYYY-MM-DD") from exc
    if args.probe_slate_date != PINNED_PROBE_SLATE_DATE:
        raise SystemExit(
            f"--probe-slate-date must be the reviewed historical slate {PINNED_PROBE_SLATE_DATE}"
        )
    lineage = _parse_json_object(
        Path(args.deploy_lineage).read_bytes(),
        label="deploy lineage",
    )
    if lineage.get("headSha") != args.expected_deploy_sha:
        raise SystemExit("Deploy lineage head SHA does not match expected deploy SHA")
    lineage_run_id = f"{lineage.get('runId')}-{lineage.get('runAttempt')}"
    if lineage_run_id != args.expected_deploy_run_id:
        raise SystemExit("Deploy lineage run/attempt does not match expected deploy run ID")
    if len(args.verifier_workflow_sha) != 40 or any(
        char not in "0123456789abcdef"
        for char in args.verifier_workflow_sha.lower()
    ):
        raise SystemExit("--verifier-workflow-sha must be an exact Git SHA")

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    evidence: Dict[str, Any] = {
        "ok": False,
        "proofType": "MLB_RESULTS_API_READ_ONLY_POSTDEPLOY_PROOF",
        "version": PROOF_VERSION,
        "createdAtUtc": _iso(_now()),
        "expectedDeploySha": args.expected_deploy_sha,
        "expectedDeployRunId": args.expected_deploy_run_id,
        "deployLineage": lineage,
        "verifierWorkflow": {
            "path": ".github/workflows/verify-mlb-results-api-read-only-postdeploy.yml",
            "workflowSha": args.verifier_workflow_sha,
            "runId": args.verifier_run_id,
            "runAttempt": args.verifier_run_attempt,
        },
        "sourceBinding": {
            path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
            for path in (
                "hello_world/mlb_results_scheduler.py",
                "hello_world/mlb_result_signals.py",
                "template.yaml",
                "scripts/mlb_lambda_artifact_identity.py",
                "scripts/verify_mlb_deploy_identity.py",
                "tests/unit/test_mlb_results_scheduler_http_read_only.py",
                "tests/unit/test_mlb_result_signals_provenance.py",
                "tests/unit/test_verify_mlb_results_api_postdeploy.py",
                "scripts/verify_mlb_results_api_postdeploy.py",
                ".github/workflows/deploy.yml",
                ".github/workflows/verify-mlb-results-api-read-only-postdeploy.yml",
            )
        },
        "stackName": args.stack_name,
        "region": args.region,
        "productionAuthorityChanged": False,
        "syntheticScheduledInvocationPerformed": False,
        "blockers": [],
    }
    exit_code = 0
    try:
        run(args, evidence)
        evidence["ok"] = True
        evidence["status"] = "VERIFIED"
        evidence["completedAtUtc"] = _iso(_now())
    except Exception as exc:
        exit_code = 1
        evidence["status"] = "FAILED"
        evidence["completedAtUtc"] = _iso(_now())
        evidence["blockers"] = [f"{type(exc).__name__}:{exc}"]
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(_json_value(evidence), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output)
    print(json.dumps(_json_value(evidence), indent=2, sort_keys=True))
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
