from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import boto3
from botocore.config import Config


VERSION = "MLB-BEDROCK-CAPACITY-CONTROL-v1-no-circuit-breaker"
DEFAULT_REGIONS: Tuple[str, ...] = ("us-east-1", "us-east-2", "us-west-2")
SERVICE_CODE = "bedrock"
PROVISIONED_REGION = "us-east-1"
PROVISIONED_MODEL_ID = "amazon.nova-lite-v1:0:24k"
PROVISIONED_MODEL_NAME = "inqsi-mlb-auto-nova-lite-1mu"
PROVISIONED_MODEL_UNITS = 1

# Quota matching is deliberately limited to the model families explicitly
# authorized for MLB AUTO. This control plane never changes model-selection,
# retry, cooldown, or throttle-routing behavior in model_gateway.py.
MODEL_ALIASES: Mapping[str, Tuple[str, ...]] = {
    "openai_gpt_oss_20b": (
        "openai gpt oss 20b",
        "gpt oss 20b",
        "gptoss20b",
    ),
    "openai_gpt_oss_120b": (
        "openai gpt oss 120b",
        "gpt oss 120b",
        "gptoss120b",
    ),
    "amazon_nova_micro": ("amazon nova micro", "nova micro"),
    "amazon_nova_lite": ("amazon nova lite", "nova lite"),
    "amazon_nova_pro": ("amazon nova pro", "nova pro"),
    "amazon_nova_2_lite": ("amazon nova 2 lite", "nova 2 lite"),
}
RELEVANT_QUOTA_TERMS: Tuple[str, ...] = (
    "token",
    "request",
    "invoke",
    "invocation",
    "throughput",
    "model unit",
)
ACTIVE_REQUEST_STATUSES = {"PENDING", "CASE_OPENED"}

_BOTO_CONFIG = Config(
    connect_timeout=10,
    read_timeout=60,
    retries={"max_attempts": 3, "mode": "standard"},
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_plain(item) for item in value]
    return value


def _error(exc: BaseException) -> Dict[str, Any]:
    response = getattr(exc, "response", {}) or {}
    error = response.get("Error") or {}
    return {
        "type": type(exc).__name__,
        "code": str(error.get("Code") or type(exc).__name__),
        "message": str(error.get("Message") or str(exc))[:2000],
    }


def normalize_name(value: Any) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).split())


def compact_name(value: Any) -> str:
    return normalize_name(value).replace(" ", "")


def matched_model_families(quota_name: str) -> List[str]:
    normalized = normalize_name(quota_name)
    compact = compact_name(quota_name)
    matches: List[str] = []
    for family, aliases in MODEL_ALIASES.items():
        if any(
            normalize_name(alias) in normalized or compact_name(alias) in compact
            for alias in aliases
        ):
            matches.append(family)
    return matches


def quota_is_relevant(quota_name: str) -> bool:
    normalized = normalize_name(quota_name)
    return bool(
        matched_model_families(quota_name)
        and any(term in normalized for term in RELEVANT_QUOTA_TERMS)
    )


def desired_quota_value(quota_name: str, current_value: Any) -> float:
    current = max(0.0, float(current_value or 0.0))
    normalized = normalize_name(quota_name)
    if "token" in normalized and "day" in normalized:
        desired = max(current * 10.0, 100_000_000.0)
    elif "token" in normalized and ("minute" in normalized or "tpm" in normalized):
        desired = max(current * 10.0, 2_000_000.0)
    elif "request" in normalized and ("minute" in normalized or "rpm" in normalized):
        desired = max(current * 10.0, 2_000.0)
    elif "request" in normalized and ("second" in normalized or "rps" in normalized):
        desired = max(current * 10.0, 100.0)
    elif "model unit" in normalized:
        desired = max(current + 1.0, float(PROVISIONED_MODEL_UNITS))
    else:
        desired = max(current * 5.0, current + 1.0)
    return float(int(desired))


def configured_regions(event: Optional[Mapping[str, Any]] = None) -> List[str]:
    requested = (event or {}).get("regions")
    if isinstance(requested, list):
        values = [str(value).strip() for value in requested if str(value).strip()]
    else:
        values = [
            value.strip()
            for value in os.environ.get(
                "MLB_AUTO_BEDROCK_CAPACITY_REGIONS", ",".join(DEFAULT_REGIONS)
            ).split(",")
            if value.strip()
        ]
    output: List[str] = []
    seen = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            output.append(value)
    return output or list(DEFAULT_REGIONS)


def _list_service_quotas(client: Any) -> List[Dict[str, Any]]:
    quotas: List[Dict[str, Any]] = []
    paginator = client.get_paginator("list_service_quotas")
    for page in paginator.paginate(ServiceCode=SERVICE_CODE):
        quotas.extend(
            dict(row) for row in (page.get("Quotas") or []) if isinstance(row, dict)
        )
    return quotas


def _active_request(client: Any, quota_code: str) -> Optional[Dict[str, Any]]:
    try:
        response = client.list_requested_service_quota_change_history_by_quota(
            ServiceCode=SERVICE_CODE,
            QuotaCode=quota_code,
        )
    except Exception:
        return None
    for row in response.get("RequestedQuotas") or []:
        if str(row.get("Status") or "").upper() in ACTIVE_REQUEST_STATUSES:
            return dict(row)
    return None


def _request_region_quotas(region: str, *, apply_changes: bool) -> Dict[str, Any]:
    client = boto3.client("service-quotas", region_name=region, config=_BOTO_CONFIG)
    result: Dict[str, Any] = {
        "region": region,
        "ok": True,
        "matchedQuotas": [],
        "submittedRequests": [],
        "existingRequests": [],
        "nonAdjustableQuotas": [],
        "errors": [],
    }
    try:
        quotas = _list_service_quotas(client)
    except Exception as exc:
        result["ok"] = False
        result["errors"].append(_error(exc))
        return result

    for quota in quotas:
        name = str(quota.get("QuotaName") or "")
        code = str(quota.get("QuotaCode") or "")
        if not code or not quota_is_relevant(name):
            continue
        current = float(quota.get("Value") or 0.0)
        desired = desired_quota_value(name, current)
        row = {
            "quotaName": name,
            "quotaCode": code,
            "currentValue": current,
            "desiredValue": desired,
            "unit": quota.get("Unit"),
            "adjustable": quota.get("Adjustable") is True,
            "modelFamilies": matched_model_families(name),
        }
        result["matchedQuotas"].append(row)
        if quota.get("Adjustable") is not True:
            result["nonAdjustableQuotas"].append(row)
            continue
        if desired <= current:
            continue
        active = _active_request(client, code)
        if active:
            result["existingRequests"].append(
                {
                    **row,
                    "status": active.get("Status"),
                    "caseId": active.get("CaseId"),
                    "existingDesiredValue": active.get("DesiredValue"),
                }
            )
            continue
        if not apply_changes:
            result["submittedRequests"].append({**row, "dryRun": True})
            continue
        try:
            submitted = client.request_service_quota_increase(
                ServiceCode=SERVICE_CODE,
                QuotaCode=code,
                DesiredValue=desired,
            )
            request = submitted.get("RequestedQuota") or {}
            result["submittedRequests"].append(
                {
                    **row,
                    "status": request.get("Status"),
                    "requestId": request.get("Id"),
                    "caseId": request.get("CaseId"),
                }
            )
        except Exception as exc:
            result["ok"] = False
            result["errors"].append({**row, **_error(exc)})
    return result


def _discover_region(region: str) -> Dict[str, Any]:
    client = boto3.client("bedrock", region_name=region, config=_BOTO_CONFIG)
    result: Dict[str, Any] = {
        "region": region,
        "ok": True,
        "foundationModels": [],
        "inferenceProfiles": [],
        "errors": [],
    }
    try:
        response = client.list_foundation_models(byOutputModality="TEXT")
        for row in response.get("modelSummaries") or []:
            model_id = str(row.get("modelId") or "")
            low = model_id.lower()
            if "openai.gpt-oss" in low or "amazon.nova" in low:
                result["foundationModels"].append(
                    {
                        "modelId": model_id,
                        "modelName": row.get("modelName"),
                        "providerName": row.get("providerName"),
                        "inferenceTypesSupported": row.get("inferenceTypesSupported") or [],
                        "lifecycle": row.get("modelLifecycle") or {},
                    }
                )
    except Exception as exc:
        result["ok"] = False
        result["errors"].append({"operation": "ListFoundationModels", **_error(exc)})
    try:
        paginator = client.get_paginator("list_inference_profiles")
        for page in paginator.paginate(typeEquals="SYSTEM_DEFINED"):
            for row in page.get("inferenceProfileSummaries") or []:
                profile_id = str(row.get("inferenceProfileId") or "")
                low = profile_id.lower()
                if "gpt-oss" in low or "nova" in low:
                    result["inferenceProfiles"].append(
                        {
                            "inferenceProfileId": profile_id,
                            "inferenceProfileName": row.get("inferenceProfileName"),
                            "status": row.get("status"),
                            "models": row.get("models") or [],
                        }
                    )
    except Exception as exc:
        result["ok"] = False
        result["errors"].append({"operation": "ListInferenceProfiles", **_error(exc)})
    return result


def _list_provisioned(client: Any) -> List[Dict[str, Any]]:
    output: List[Dict[str, Any]] = []
    paginator = client.get_paginator("list_provisioned_model_throughputs")
    for page in paginator.paginate(nameContains=PROVISIONED_MODEL_NAME):
        output.extend(
            dict(row)
            for row in (page.get("provisionedModelSummaries") or [])
            if isinstance(row, dict)
        )
    return output


def _provision_dedicated_capacity(*, purchase: bool) -> Dict[str, Any]:
    client = boto3.client("bedrock", region_name=PROVISIONED_REGION, config=_BOTO_CONFIG)
    result: Dict[str, Any] = {
        "region": PROVISIONED_REGION,
        "modelId": PROVISIONED_MODEL_ID,
        "provisionedModelName": PROVISIONED_MODEL_NAME,
        "modelUnits": PROVISIONED_MODEL_UNITS,
        "commitmentDuration": None,
        "noCommitment": True,
        "purchaseRequested": bool(purchase),
        "created": False,
        "ok": True,
        "existing": [],
    }
    try:
        existing = _list_provisioned(client)
        result["existing"] = existing
    except Exception as exc:
        result["ok"] = False
        result["error"] = _error(exc)
        return result
    live = [
        row
        for row in existing
        if str(row.get("provisionedModelName") or "") == PROVISIONED_MODEL_NAME
        and str(row.get("status") or "") in {"Creating", "InService", "Updating"}
    ]
    if live:
        result["status"] = str(live[0].get("status") or "")
        result["provisionedModelArn"] = live[0].get("provisionedModelArn")
        return result
    if not purchase:
        result["status"] = "DRY_RUN"
        return result
    token = hashlib.sha256(
        f"{PROVISIONED_REGION}:{PROVISIONED_MODEL_NAME}:{PROVISIONED_MODEL_ID}:1".encode(
            "utf-8"
        )
    ).hexdigest()
    try:
        response = client.create_provisioned_model_throughput(
            clientRequestToken=token,
            modelUnits=PROVISIONED_MODEL_UNITS,
            provisionedModelName=PROVISIONED_MODEL_NAME,
            modelId=PROVISIONED_MODEL_ID,
            tags=[
                {"key": "Application", "value": "Inqsi-MLB-AUTO"},
                {"key": "CapacityMode", "value": "NoCommitment"},
            ],
        )
        result.update(
            {
                "created": True,
                "status": "Creating",
                "provisionedModelArn": response.get("provisionedModelArn"),
            }
        )
    except Exception as exc:
        result["ok"] = False
        result["status"] = "CREATE_BLOCKED"
        result["error"] = _error(exc)
    return result


def lambda_handler(event: Any, context: Any) -> Dict[str, Any]:
    request = event if isinstance(event, Mapping) else {}
    apply_changes = request.get("requestQuotaIncreases", True) is True
    purchase = request.get("purchaseProvisionedThroughput", True) is True
    regions = configured_regions(request)
    report: Dict[str, Any] = {
        "proofType": "MLB_BEDROCK_CAPACITY_AND_IAM",
        "version": VERSION,
        "createdAtUtc": _now(),
        "regions": regions,
        "requestQuotaIncreases": apply_changes,
        "purchaseProvisionedThroughput": purchase,
        "quotaRequests": [],
        "discovery": [],
        "dedicatedCapacity": {},
        "circuitBreakerAdded": False,
        "modelGatewayChanged": False,
        "productionAuthorityChanged": False,
        "secretExposed": False,
    }
    for region in regions:
        report["discovery"].append(_discover_region(region))
        report["quotaRequests"].append(
            _request_region_quotas(region, apply_changes=apply_changes)
        )
    report["dedicatedCapacity"] = _provision_dedicated_capacity(purchase=purchase)
    quota_ok = all(row.get("ok") is True for row in report["quotaRequests"])
    discovery_ok = any(row.get("ok") is True for row in report["discovery"])
    dedicated = report["dedicatedCapacity"]
    dedicated_ok = bool(
        dedicated.get("ok") is True
        or (dedicated.get("error") or {}).get("code")
        in {"ServiceQuotaExceededException", "ThrottlingException"}
    )
    report["ok"] = bool(quota_ok and discovery_ok and dedicated_ok)
    report["completedAtUtc"] = _now()
    return _plain(report)
