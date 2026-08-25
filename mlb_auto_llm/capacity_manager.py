from __future__ import annotations

import io
import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import boto3
from botocore.config import Config

try:
    import bedrock_smoke
except ModuleNotFoundError:  # pragma: no cover - package import in unit tests
    from mlb_auto_llm import bedrock_smoke


VERSION = "MLB-AUTO-BEDROCK-CAPACITY-MANAGER-v1-lambda-role-quota-ramp"
DEFAULT_REGIONS = ("us-east-1", "us-west-2", "us-east-2")
DEFAULT_DESIRED_MULTIPLIER = 10.0
DEFAULT_MAX_QUOTA_REQUESTS = 30
DEFAULT_MAX_ROUTES_PER_REGION = 32
MAX_QUOTA_REQUESTS = 60
MAX_ROUTES_PER_REGION = 48
ACTIVE_QUOTA_REQUEST_STATUSES = frozenset({"PENDING", "CASE_OPENED"})
ACCEPTED_QUOTA_REQUEST_STATUSES = frozenset(
    {"PENDING", "CASE_OPENED", "APPROVED"}
)
THROUGHPUT_TERMS = (
    "tokens per minute",
    "tokens per day",
    "requests per minute",
    "input tokens per minute",
    "output tokens per minute",
    "model units",
    "provisioned throughput",
)
TEXT_PROVIDER_TERMS = (
    "amazon",
    "nova",
    "titan",
    "openai",
    "gpt",
    "anthropic",
    "claude",
    "meta",
    "llama",
    "mistral",
    "cohere",
    "command",
    "ai21",
    "jamba",
    "deepseek",
    "qwen",
    "xai",
    "grok",
    "writer",
    "palmyra",
    "nvidia",
    "nemotron",
    "moonshot",
    "kimi",
    "zai",
    "glm",
)
NON_TEXT_TERMS = (
    "embed",
    "embedding",
    "rerank",
    "image",
    "canvas",
    "video",
    "audio",
    "speech",
    "guardrail",
    "moderation",
    "reel",
)

_CLIENT_CONFIG = Config(
    connect_timeout=5,
    read_timeout=35,
    retries={"mode": "standard", "total_max_attempts": 2},
)
_SMOKE_CLIENT_CONFIG = Config(
    connect_timeout=5,
    read_timeout=35,
    retries={"mode": "standard", "total_max_attempts": 1},
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bounded_int(value: Any, default: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(maximum, max(1, parsed))


def _bounded_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return min(maximum, max(minimum, parsed))


def _regions(event: Mapping[str, Any]) -> List[str]:
    configured = event.get("regions")
    if isinstance(configured, str):
        values = configured.split(",")
    elif isinstance(configured, Sequence) and not isinstance(
        configured, (str, bytes, bytearray)
    ):
        values = list(configured)
    else:
        values = os.environ.get(
            "MLB_AUTO_BEDROCK_REGIONS", ",".join(DEFAULT_REGIONS)
        ).split(",")
    output: List[str] = []
    seen = set()
    for raw in values:
        value = str(raw or "").strip()
        if value and value not in seen:
            seen.add(value)
            output.append(value)
    return output or list(DEFAULT_REGIONS)


def _redact(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"arn:aws[a-zA-Z-]*:[^\s,]+", "[REDACTED_ARN]", text)
    text = re.sub(r"(?<!\d)\d{12}(?!\d)", "[REDACTED_ACCOUNT]", text)
    text = re.sub(
        r"(?i)(authorization|x-api-key|token|secret|password)\s*[:=]\s*[^\s,]+",
        r"\1=[REDACTED]",
        text,
    )
    return text[:700]


def _error(exc: BaseException) -> Dict[str, Any]:
    response = getattr(exc, "response", {}) or {}
    metadata = response.get("ResponseMetadata") or {}
    return {
        "errorCode": str(
            (response.get("Error") or {}).get("Code") or type(exc).__name__
        ),
        "message": _redact(exc),
        "httpStatusCode": metadata.get("HTTPStatusCode"),
        "requestId": metadata.get("RequestId"),
    }


def _pages(
    client: Any,
    method: str,
    *,
    token_key: str,
    max_pages: int = 100,
    **kwargs: Any,
) -> Iterable[Dict[str, Any]]:
    token: Optional[str] = None
    for _ in range(max_pages):
        request = dict(kwargs)
        if token:
            request[token_key] = token
        response = getattr(client, method)(**request)
        yield response
        token = str(
            response.get(token_key)
            or response.get("NextToken")
            or response.get("nextToken")
            or ""
        ).strip()
        if not token:
            return


def _text_candidate(model_id: str) -> bool:
    value = str(model_id or "").strip().lower()
    return bool(value) and not any(term in value for term in NON_TEXT_TERMS)


def _route_priority(model_id: str) -> Tuple[int, int, str]:
    value = str(model_id or "").lower()
    global_profile = value.startswith("global.")
    regional_profile = value.startswith(("us.", "eu.", "apac."))
    small = any(
        term in value
        for term in (
            "micro",
            "lite",
            "mini",
            "small",
            "flash",
            "haiku",
            "nano",
            "7b",
            "8b",
            "9b",
            "12b",
            "20b",
        )
    )
    if global_profile and small:
        tier = 0
    elif global_profile:
        tier = 1
    elif regional_profile and small:
        tier = 2
    elif regional_profile:
        tier = 3
    elif small:
        tier = 4
    else:
        tier = 5
    return tier, len(value), value


def _relevant_quota(row: Mapping[str, Any]) -> bool:
    name = str(row.get("QuotaName") or "")
    value = name.lower()
    return bool(
        any(term in value for term in THROUGHPUT_TERMS)
        and any(term in value for term in TEXT_PROVIDER_TERMS)
        and not any(term in value for term in NON_TEXT_TERMS)
    )


def _quota_priority(row: Mapping[str, Any]) -> Tuple[int, int, str]:
    name = str(row.get("QuotaName") or "")
    value = name.lower()
    if "tokens per day" in value:
        tier = 0
    elif "cross-region" in value and "tokens per minute" in value:
        tier = 1
    elif "tokens per minute" in value:
        tier = 2
    elif "requests per minute" in value:
        tier = 3
    elif "model units" in value or "provisioned throughput" in value:
        tier = 4
    else:
        tier = 5
    adjustable_penalty = 0 if row.get("Adjustable") is True else 1
    return tier, adjustable_penalty, name


def _desired_quota_value(name: str, current: float, multiplier: float) -> float:
    value = str(name or "").lower()
    if "tokens per day" in value:
        return max(current * multiplier, current + 1_000_000.0)
    if "tokens per minute" in value:
        return max(current * multiplier, current + 100_000.0)
    if "requests per minute" in value:
        return max(current * multiplier, current + 100.0)
    if "model units" in value or "provisioned throughput" in value:
        return max(current + 1.0, 1.0)
    return max(current * multiplier, current + 1.0)


def _quota_inventory(region: str) -> Dict[str, Any]:
    output: Dict[str, Any] = {"quotas": [], "errors": []}
    try:
        client = boto3.client(
            "service-quotas", region_name=region, config=_CLIENT_CONFIG
        )
        for response in _pages(
            client,
            "list_service_quotas",
            token_key="NextToken",
            ServiceCode="bedrock",
            MaxResults=100,
        ):
            for row in response.get("Quotas") or []:
                if not isinstance(row, dict) or not _relevant_quota(row):
                    continue
                output["quotas"].append(
                    {
                        key: row.get(key)
                        for key in (
                            "QuotaName",
                            "QuotaCode",
                            "Value",
                            "Adjustable",
                            "GlobalQuota",
                        )
                    }
                )
        output["quotas"] = sorted(output["quotas"], key=_quota_priority)
    except Exception as exc:
        output["errors"].append(_error(exc))
    return output


def _model_inventory(region: str) -> Dict[str, Any]:
    output: Dict[str, Any] = {
        "activeInferenceProfiles": [],
        "activeOnDemandModels": [],
        "errors": [],
    }
    try:
        client = boto3.client("bedrock", region_name=region, config=_CLIENT_CONFIG)
        profiles: List[str] = []
        for response in _pages(
            client,
            "list_inference_profiles",
            token_key="nextToken",
            typeEquals="SYSTEM_DEFINED",
            maxResults=100,
        ):
            for row in response.get("inferenceProfileSummaries") or []:
                if not isinstance(row, dict) or row.get("status") != "ACTIVE":
                    continue
                model_id = str(row.get("inferenceProfileId") or "").strip()
                if _text_candidate(model_id):
                    profiles.append(model_id)
        models: List[str] = []
        response = client.list_foundation_models(
            byOutputModality="TEXT", byInferenceType="ON_DEMAND"
        )
        for row in response.get("modelSummaries") or []:
            if not isinstance(row, dict):
                continue
            lifecycle = row.get("modelLifecycle") or {}
            model_id = str(row.get("modelId") or "").strip()
            if (
                str(lifecycle.get("status") or "ACTIVE").upper()
                != "END_OF_LIFE"
                and _text_candidate(model_id)
            ):
                models.append(model_id)
        output["activeInferenceProfiles"] = sorted(
            set(profiles), key=_route_priority
        )
        output["activeOnDemandModels"] = sorted(
            set(models), key=_route_priority
        )
    except Exception as exc:
        output["errors"].append(_error(exc))
    return output


def _active_quota_request(client: Any, quota_code: str) -> Optional[Dict[str, Any]]:
    try:
        response = client.list_requested_service_quota_change_history_by_quota(
            ServiceCode="bedrock", QuotaCode=quota_code
        )
    except Exception:
        return None
    for row in response.get("RequestedQuotas") or []:
        if str(row.get("Status") or "") in ACTIVE_QUOTA_REQUEST_STATUSES:
            return row
    return None


def _request_quota_increases(
    inventories: Mapping[str, Mapping[str, Any]],
    *,
    multiplier: float,
    maximum: int,
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    submitted = 0
    global_seen = set()
    for region, inventory in inventories.items():
        try:
            client = boto3.client(
                "service-quotas", region_name=region, config=_CLIENT_CONFIG
            )
        except Exception as exc:
            results.append({"region": region, "status": "CLIENT_UNAVAILABLE", **_error(exc)})
            continue
        for row in inventory.get("quotas") or []:
            if submitted >= maximum:
                return results
            if not isinstance(row, dict) or row.get("Adjustable") is not True:
                continue
            quota_code = str(row.get("QuotaCode") or "").strip()
            quota_name = str(row.get("QuotaName") or "").strip()
            if not quota_code:
                continue
            global_key = quota_code if row.get("GlobalQuota") is True else None
            if global_key and global_key in global_seen:
                continue
            try:
                current = float(row.get("Value") or 0.0)
            except (TypeError, ValueError):
                current = 0.0
            desired = _desired_quota_value(quota_name, current, multiplier)
            existing = _active_quota_request(client, quota_code)
            if existing:
                result = {
                    "region": region,
                    "quotaName": quota_name,
                    "quotaCode": quota_code,
                    "current": current,
                    "desired": desired,
                    "status": existing.get("Status"),
                    "requestId": existing.get("Id"),
                    "existingRequest": True,
                }
                results.append(result)
                if global_key:
                    global_seen.add(global_key)
                continue
            try:
                response = client.request_service_quota_increase(
                    ServiceCode="bedrock",
                    QuotaCode=quota_code,
                    DesiredValue=desired,
                )
                requested = response.get("RequestedQuota") or {}
                results.append(
                    {
                        "region": region,
                        "quotaName": quota_name,
                        "quotaCode": quota_code,
                        "current": current,
                        "desired": desired,
                        "status": requested.get("Status"),
                        "requestId": requested.get("Id"),
                        "existingRequest": False,
                    }
                )
                submitted += 1
                if global_key:
                    global_seen.add(global_key)
            except Exception as exc:
                results.append(
                    {
                        "region": region,
                        "quotaName": quota_name,
                        "quotaCode": quota_code,
                        "current": current,
                        "desired": desired,
                        "status": "REQUEST_FAILED",
                        **_error(exc),
                    }
                )
    return results


def _titan_text(runtime: Any, model_id: str) -> str:
    response = runtime.invoke_model(
        modelId=model_id,
        contentType="application/json",
        accept="application/json",
        body=json.dumps(
            {
                "inputText": "Return exactly: OK",
                "textGenerationConfig": {
                    "maxTokenCount": 8,
                    "temperature": 0,
                    "topP": 0.9,
                    "stopSequences": [],
                },
            }
        ).encode("utf-8"),
    )
    stream = response.get("body")
    raw = stream.read() if hasattr(stream, "read") else stream
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    payload = json.loads(str(raw or "{}"))
    rows = payload.get("results") or []
    row = rows[0] if rows and isinstance(rows[0], dict) else {}
    return str(row.get("outputText") or "").strip()


def _converse_text(runtime: Any, model_id: str) -> str:
    response = runtime.converse(
        modelId=model_id,
        messages=[
            {"role": "user", "content": [{"text": "Return exactly: OK"}]}
        ],
        inferenceConfig={"maxTokens": 8, "temperature": 0},
    )
    blocks = (
        ((response.get("output") or {}).get("message") or {}).get("content")
        or []
    )
    return "".join(
        str(block.get("text") or "")
        for block in blocks
        if isinstance(block, dict)
    ).strip()


def _direct_runtime_smoke(
    region: str,
    routes: Sequence[str],
    *,
    maximum: int,
) -> Dict[str, Any]:
    output: Dict[str, Any] = {
        "ok": False,
        "region": region,
        "attempts": [],
        "selectedRoute": None,
    }
    try:
        runtime = boto3.client(
            "bedrock-runtime", region_name=region, config=_SMOKE_CLIENT_CONFIG
        )
    except Exception as exc:
        output["attempts"].append(_error(exc))
        return output
    for model_id in list(routes)[:maximum]:
        started = time.monotonic()
        try:
            text = (
                _titan_text(runtime, model_id)
                if model_id.startswith("amazon.titan-text-")
                else _converse_text(runtime, model_id)
            )
            row = {
                "modelId": model_id,
                "ok": bool(text),
                "latencyMs": round((time.monotonic() - started) * 1000),
                "responseNonEmpty": bool(text),
            }
            output["attempts"].append(row)
            if text:
                output["ok"] = True
                output["selectedRoute"] = model_id
                return output
        except Exception as exc:
            output["attempts"].append(
                {
                    "modelId": model_id,
                    "ok": False,
                    "latencyMs": round((time.monotonic() - started) * 1000),
                    **_error(exc),
                }
            )
    return output


def _sanitize_application_smoke(value: Any) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        return {"ok": False, "errors": [{"errorCode": "INVALID_SMOKE_RESPONSE"}]}
    errors: List[Dict[str, Any]] = []
    for row in value.get("errors") or value.get("errorsBeforeSuccess") or []:
        if not isinstance(row, Mapping):
            continue
        errors.append(
            {
                key: (
                    _redact(row.get(key))
                    if key == "message"
                    else row.get(key)
                )
                for key in (
                    "routeId",
                    "region",
                    "modelId",
                    "endpointFamily",
                    "errorCode",
                    "message",
                )
                if row.get(key) is not None
            }
        )
    return {
        "ok": value.get("ok") is True,
        "routeId": value.get("routeId"),
        "region": value.get("region"),
        "modelId": value.get("modelId"),
        "endpointFamily": value.get("endpointFamily"),
        "responseNonEmpty": value.get("responseNonEmpty") is True,
        "configuredRegions": value.get("configuredRegions") or [],
        "configuredModelCount": value.get("configuredModelCount"),
        "configuredRouteCatalogCount": value.get("configuredRouteCatalogCount"),
        "smokeRouteLimit": value.get("smokeRouteLimit"),
        "mantleModelCount": value.get("mantleModelCount"),
        "runtimeModelCount": value.get("runtimeModelCount"),
        "attemptedModelIds": value.get("attemptedModelIds") or [],
        "errors": errors,
    }


def lambda_handler(event: Any, context: Any) -> Dict[str, Any]:
    request = event if isinstance(event, Mapping) else {}
    regions = _regions(request)
    multiplier = _bounded_float(
        request.get("desiredMultiplier"),
        DEFAULT_DESIRED_MULTIPLIER,
        1.01,
        100.0,
    )
    maximum_quota_requests = _bounded_int(
        request.get("maxQuotaRequests"),
        DEFAULT_MAX_QUOTA_REQUESTS,
        MAX_QUOTA_REQUESTS,
    )
    maximum_routes = _bounded_int(
        request.get("maxRoutesPerRegion"),
        DEFAULT_MAX_ROUTES_PER_REGION,
        MAX_ROUTES_PER_REGION,
    )
    request_increases = request.get("requestQuotaIncreases", True) is not False

    quota_inventory: Dict[str, Dict[str, Any]] = {}
    model_inventory: Dict[str, Dict[str, Any]] = {}
    for region in regions:
        quota_inventory[region] = _quota_inventory(region)
        model_inventory[region] = _model_inventory(region)

    quota_requests = (
        _request_quota_increases(
            quota_inventory,
            multiplier=multiplier,
            maximum=maximum_quota_requests,
        )
        if request_increases
        else []
    )

    direct_smoke: List[Dict[str, Any]] = []
    for region in regions:
        inventory = model_inventory.get(region) or {}
        routes = list(
            dict.fromkeys(
                [
                    *(inventory.get("activeInferenceProfiles") or []),
                    *(inventory.get("activeOnDemandModels") or []),
                ]
            )
        )
        routes = sorted(routes, key=_route_priority)
        direct_smoke.append(
            _direct_runtime_smoke(region, routes, maximum=maximum_routes)
        )

    try:
        application_smoke = _sanitize_application_smoke(
            bedrock_smoke.lambda_handler({}, context)
        )
    except Exception as exc:
        application_smoke = {"ok": False, "errors": [_error(exc)]}

    successful_direct = [row for row in direct_smoke if row.get("ok") is True]
    accepted_requests = [
        row
        for row in quota_requests
        if str(row.get("status") or "") in ACCEPTED_QUOTA_REQUEST_STATUSES
    ]
    submitted_requests = [
        row
        for row in accepted_requests
        if row.get("existingRequest") is False
    ]
    live_capacity_ok = bool(
        application_smoke.get("ok") is True or successful_direct
    )
    result = {
        "ok": live_capacity_ok,
        "version": VERSION,
        "proofType": "MLB_AUTO_BEDROCK_CAPACITY_RAMP",
        "createdAtUtc": _now_iso(),
        "capacityManagerRoleUsed": True,
        "regions": regions,
        "quotaInventory": quota_inventory,
        "modelInventory": model_inventory,
        "quotaIncreaseRequests": quota_requests,
        "quotaIncreaseAcceptedOrPendingCount": len(accepted_requests),
        "quotaIncreaseSubmittedCount": len(submitted_requests),
        "quotaIncreaseRequestFailureCount": sum(
            row.get("status") == "REQUEST_FAILED" for row in quota_requests
        ),
        "requestedScalePolicy": {
            "desiredMultiplier": multiplier,
            "maximumQuotaRequests": maximum_quota_requests,
            "maximumRoutesPerRegion": maximum_routes,
            "tokenQuotaMinimumAbsoluteIncrease": {
                "perDay": 1_000_000,
                "perMinute": 100_000,
            },
            "requestQuotaMinimumAbsoluteIncreasePerMinute": 100,
            "modelUnitMinimumAbsoluteIncrease": 1,
        },
        "applicationSmoke": application_smoke,
        "directRuntimeSmoke": direct_smoke,
        "liveCapacityOk": live_capacity_ok,
        "successfulRegions": sorted(
            {
                str(row.get("region"))
                for row in successful_direct
                if row.get("region")
            }
            | (
                {str(application_smoke.get("region"))}
                if application_smoke.get("ok") is True
                and application_smoke.get("region")
                else set()
            )
        ),
        "mainLambdaMemoryMb": 10240,
        "mainLambdaEphemeralStorageMb": 2048,
        "productionRouteAttemptCeiling": 80,
        "smokeRouteAttemptCeiling": 16,
        "productionAuthorityChanged": False,
        "automaticWagerAllowed": False,
        "secretExposed": False,
        "conclusion": "PASS" if live_capacity_ok else "CAPACITY_REQUESTED_WAITING",
    }
    return result
