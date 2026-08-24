from __future__ import annotations

import json
import os
import re
import time
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import boto3
from botocore.config import Config


ROUTE_SEPARATOR = "::"
_REGION_PATTERN = re.compile(r"^[a-z]{2}(?:-gov)?-[a-z]+-\d$")

# The first production recovery exhausted every configured Nova route before
# reaching another provider. Keep the rescue set deliberately provider- and
# Region-diverse so a family-level daily token bucket cannot consume the whole
# failover window.
DIVERSIFIED_RECOVERY_ROUTES = (
    "us-west-2::meta.llama3-2-1b-instruct-v1:0",
    "us-west-2::meta.llama3-2-3b-instruct-v1:0",
    "us-west-2::mistral.mistral-small-2402-v1:0",
    "us-west-2::mistral.ministral-3-3b-instruct",
    "us-west-2::ai21.jamba-1-5-mini-v1:0",
    "us-west-2::openai.gpt-oss-20b-1:0",
    "us-west-2::amazon.nova-micro-v1:0",
    "us-west-2::amazon.nova-lite-v1:0",
    "us-east-2::meta.llama3-2-1b-instruct-v1:0",
    "us-east-2::meta.llama3-2-3b-instruct-v1:0",
    "us-east-2::amazon.nova-micro-v1:0",
    "us-east-2::amazon.nova-lite-v1:0",
    "meta.llama3-2-1b-instruct-v1:0",
    "meta.llama3-2-3b-instruct-v1:0",
    "mistral.mistral-small-2402-v1:0",
    "mistral.ministral-3-3b-instruct",
    "ai21.jamba-1-5-mini-v1:0",
    "google.gemma-3-4b-it",
    "google.gemma-3n-e2b-it",
    "openai.gpt-oss-20b-1:0",
    "zai.glm-4.7-flash",
)

NOVA_LAST_RESORT_ROUTES = (
    "amazon.nova-micro-v1:0",
    "amazon.nova-lite-v1:0",
    "amazon.nova-pro-v1:0",
    "amazon.nova-2-lite-v1:0",
    "us.amazon.nova-micro-v1:0",
    "us.amazon.nova-lite-v1:0",
    "global.amazon.nova-2-lite-v1:0",
    "us.amazon.nova-2-lite-v1:0",
    "us.amazon.nova-pro-v1:0",
)

DEFAULT_MODELS = DIVERSIFIED_RECOVERY_ROUTES + NOVA_LAST_RESORT_ROUTES + (
    "openai.gpt-5.6-sol",
    "anthropic.claude-opus-4-8",
    "anthropic.claude-opus-4-7",
    "anthropic.claude-opus-4-6-v1",
    "anthropic.claude-sonnet-4-6-v1",
)

_PREFERRED_MODEL: Optional[str] = None
_MODEL_FAILURE_UNTIL: Dict[str, float] = {}
_RUNTIME_CLIENTS: Dict[str, Any] = {}
_CONTROL_CLIENTS: Dict[str, Any] = {}

# Fail over quickly instead of allowing one unavailable or throttled model to
# consume the complete Lambda timeout through SDK retries.
_BOTO_CONFIG = Config(
    connect_timeout=5,
    read_timeout=25,
    retries={"max_attempts": 1, "mode": "standard"},
)


def _region() -> str:
    return (
        os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or "us-east-1"
    )


def configured_regions() -> List[str]:
    configured = [
        value.strip()
        for value in os.environ.get(
            "MLB_AUTO_BEDROCK_REGIONS", f"{_region()},us-west-2,us-east-2"
        ).split(",")
        if value.strip()
    ]
    return _dedupe((_region(), *configured))


def _split_route(route_id: str) -> Tuple[str, str]:
    value = str(route_id or "").strip()
    if ROUTE_SEPARATOR in value:
        region, model_id = value.split(ROUTE_SEPARATOR, 1)
        if _REGION_PATTERN.match(region) and model_id:
            return region, model_id
    return _region(), value


def _format_route(region: str, model_id: str) -> str:
    return model_id if region == _region() else f"{region}{ROUTE_SEPARATOR}{model_id}"


def _runtime_client(region: Optional[str] = None) -> Any:
    target = region or _region()
    if target not in _RUNTIME_CLIENTS:
        _RUNTIME_CLIENTS[target] = boto3.client(
            "bedrock-runtime", region_name=target, config=_BOTO_CONFIG
        )
    return _RUNTIME_CLIENTS[target]


def _control_client(region: Optional[str] = None) -> Any:
    target = region or _region()
    if target not in _CONTROL_CLIENTS:
        _CONTROL_CLIENTS[target] = boto3.client(
            "bedrock", region_name=target, config=_BOTO_CONFIG
        )
    return _CONTROL_CLIENTS[target]


def _dedupe(values: Iterable[str]) -> List[str]:
    output: List[str] = []
    seen = set()
    for raw in values:
        value = str(raw or "").strip()
        if value and value not in seen:
            seen.add(value)
            output.append(value)
    return output


def _model_priority(route_id: str) -> tuple[int, int, str]:
    region, model_id = _split_route(route_id)
    value = model_id.lower()
    alternate_region = region != _region()
    small = any(
        token in value
        for token in (
            "micro",
            "mini",
            "lite",
            "small",
            "1b",
            "2b",
            "3b",
            "4b",
            "7b",
            "8b",
            "9b",
            "12b",
            "14b",
            "20b",
            "haiku",
            "flash",
        )
    )
    if small and not value.startswith("amazon.nova"):
        return (0 if alternate_region else 1, 0, route_id)
    if alternate_region and value.startswith("amazon.nova"):
        return (2, 0, route_id)
    if value.startswith("amazon.nova"):
        return (3, 0, route_id)
    if value.startswith(("us.", "global.")):
        return (4, 0, route_id)
    if value.startswith(("openai.gpt-5", "anthropic.claude-opus")):
        return (7, 0, route_id)
    return (5 if alternate_region else 6, 0, route_id)


def _discover_region(region: str) -> List[str]:
    candidates: List[str] = []
    client = _control_client(region)

    try:
        response = client.list_inference_profiles(
            typeEquals="SYSTEM_DEFINED", maxResults=1000
        )
        for row in response.get("inferenceProfileSummaries") or []:
            if not isinstance(row, dict) or row.get("status") != "ACTIVE":
                continue
            model_id = str(row.get("inferenceProfileId") or "").strip()
            if model_id:
                candidates.append(_format_route(region, model_id))
    except Exception:
        pass

    try:
        response = client.list_foundation_models(
            byOutputModality="TEXT", byInferenceType="ON_DEMAND"
        )
        for row in response.get("modelSummaries") or []:
            if not isinstance(row, dict):
                continue
            lifecycle = row.get("modelLifecycle") or {}
            if str(lifecycle.get("status") or "ACTIVE").upper() == "END_OF_LIFE":
                continue
            model_id = str(row.get("modelId") or "").strip()
            if model_id:
                candidates.append(_format_route(region, model_id))
    except Exception:
        pass

    return candidates


@lru_cache(maxsize=1)
def discovered_models() -> List[str]:
    """List account-visible text routes across approved Bedrock Regions.

    Discovery remains best-effort. Static diversified routes stay available when
    a control-plane call is unavailable or not authorized in one Region.
    """

    if os.environ.get("MLB_AUTO_BEDROCK_DISCOVERY", "true").lower() in {
        "0",
        "false",
        "no",
    }:
        return []

    candidates: List[str] = []
    for region in configured_regions():
        candidates.extend(_discover_region(region))
    return sorted(_dedupe(candidates), key=_model_priority)


def configured_models() -> List[str]:
    configured = [
        value.strip()
        for value in os.environ.get(
            "MLB_AUTO_BEDROCK_MODELS", ",".join(DEFAULT_MODELS)
        ).split(",")
        if value.strip()
    ]
    # Static provider diversity is first, live account discovery second, and
    # already exhausted Nova/Mantle families are last-resort only.
    return _dedupe(
        (
            *DIVERSIFIED_RECOVERY_ROUTES,
            *discovered_models(),
            *configured,
            *NOVA_LAST_RESORT_ROUTES,
        )
    )


def _endpoint_family(route_id: str) -> str:
    _, model_id = _split_route(route_id)
    if model_id.startswith("openai.gpt-5"):
        return "bedrock-mantle-openai"
    if model_id.startswith("anthropic."):
        return "bedrock-mantle-anthropic"
    return "bedrock-runtime-converse"


def _bearer_token() -> str:
    from aws_bedrock_token_generator import provide_token

    token = str(provide_token() or "").strip()
    if not token:
        raise RuntimeError("EMPTY_BEDROCK_BEARER_TOKEN")
    return token


def _post_json(
    url: str,
    headers: Dict[str, str],
    payload: Dict[str, Any],
    timeout: int = 75,
) -> Dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            parsed = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP_{exc.code}:{body[:1200]}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("BEDROCK_MANTLE_RESPONSE_NOT_OBJECT")
    return parsed


def _openai_text(payload: Dict[str, Any]) -> str:
    if payload.get("output_text"):
        return str(payload["output_text"])
    parts: List[str] = []
    for item in payload.get("output") or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content") or []:
            if not isinstance(content, dict):
                continue
            if content.get("type") in {"output_text", "text"}:
                parts.append(str(content.get("text") or ""))
    return "".join(parts).strip()


def _invoke_openai(
    model_id: str, prompt: str, *, max_tokens: int, region: str
) -> Dict[str, Any]:
    payload = _post_json(
        f"https://bedrock-mantle.{region}.api.aws/openai/v1/responses",
        {
            "Authorization": f"Bearer {_bearer_token()}",
            "Content-Type": "application/json",
        },
        {
            "model": model_id,
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": prompt}],
                }
            ],
            "max_output_tokens": int(max_tokens),
            "store": False,
        },
    )
    text = _openai_text(payload)
    if not text:
        raise RuntimeError("EMPTY_BEDROCK_MANTLE_OPENAI_RESPONSE")
    return {
        "text": text,
        "usage": dict(payload.get("usage") or {}),
        "endpointFamily": "bedrock-mantle-openai",
        "region": region,
        "modelId": model_id,
    }


def _invoke_anthropic(
    model_id: str,
    prompt: str,
    *,
    max_tokens: int,
    temperature: float,
    region: str,
) -> Dict[str, Any]:
    payload = _post_json(
        f"https://bedrock-mantle.{region}.api.aws/anthropic/v1/messages",
        {
            "x-api-key": _bearer_token(),
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        {
            "model": model_id,
            "max_tokens": int(max_tokens),
            "temperature": float(temperature),
            "messages": [{"role": "user", "content": prompt}],
        },
    )
    text = "".join(
        str(item.get("text") or "")
        for item in payload.get("content") or []
        if isinstance(item, dict) and item.get("type") == "text"
    ).strip()
    if not text:
        raise RuntimeError("EMPTY_BEDROCK_MANTLE_ANTHROPIC_RESPONSE")
    return {
        "text": text,
        "usage": dict(payload.get("usage") or {}),
        "endpointFamily": "bedrock-mantle-anthropic",
        "region": region,
        "modelId": model_id,
    }


def invoke_text(
    route_id: str,
    prompt: str,
    *,
    client: Any = None,
    max_tokens: int = 900,
    temperature: float = 0.0,
    top_p: float = 0.9,
) -> Dict[str, Any]:
    region, model_id = _split_route(route_id)
    if model_id.startswith("openai.gpt-5"):
        return _invoke_openai(
            model_id, prompt, max_tokens=max_tokens, region=region
        )
    if model_id.startswith("anthropic."):
        return _invoke_anthropic(
            model_id,
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            region=region,
        )

    runtime = client or _runtime_client(region)
    response = runtime.converse(
        modelId=model_id,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={
            "maxTokens": int(max_tokens),
            "temperature": float(temperature),
            "topP": float(top_p),
        },
    )
    blocks = (
        ((response.get("output") or {}).get("message") or {}).get("content") or []
    )
    text = "\n".join(
        str(block.get("text") or "")
        for block in blocks
        if isinstance(block, dict)
    ).strip()
    if not text:
        raise RuntimeError("EMPTY_BEDROCK_CONVERSE_RESPONSE")
    return {
        "text": text,
        "usage": dict(response.get("usage") or {}),
        "endpointFamily": "bedrock-runtime-converse",
        "region": region,
        "modelId": model_id,
    }


def _ordered_models(models: Iterable[str]) -> List[str]:
    values = _dedupe(models)
    if _PREFERRED_MODEL and _PREFERRED_MODEL in values:
        return [_PREFERRED_MODEL] + [
            value for value in values if value != _PREFERRED_MODEL
        ]
    return values


def _failure_cooldown_seconds(code: str, message: str) -> int:
    normalized = f"{code} {message}".lower()
    if "too many tokens per day" in normalized:
        return 1800
    if "throttl" in normalized or "too many tokens" in normalized:
        return 90
    if any(
        token in normalized
        for token in (
            "accessdenied",
            "access denied",
            "permission",
            "not available for this account",
            "does not exist",
            "validationexception",
            "resource not found",
            "unsupported model",
        )
    ):
        return 21600
    return 30


def invoke_chain_text(
    prompt: str,
    models: Optional[Iterable[str]] = None,
    *,
    client: Any = None,
    max_tokens: int = 900,
    temperature: float = 0.0,
    top_p: float = 0.9,
) -> Dict[str, Any]:
    global _PREFERRED_MODEL

    explicit_models = models is not None
    discovered_count = None if explicit_models else len(discovered_models())
    now = time.time()
    attempted: List[str] = []
    errors: List[Dict[str, Any]] = []
    max_attempts = max(
        1, int(os.environ.get("MLB_AUTO_BEDROCK_MAX_MODEL_ATTEMPTS", "24"))
    )

    eligible: List[str] = []
    for route_id in _ordered_models(models or configured_models()):
        retry_at = float(_MODEL_FAILURE_UNTIL.get(route_id) or 0.0)
        if retry_at > now:
            region, model_id = _split_route(route_id)
            errors.append(
                {
                    "routeId": route_id,
                    "region": region,
                    "modelId": model_id,
                    "endpointFamily": _endpoint_family(route_id),
                    "errorCode": "MODEL_COOLDOWN",
                    "message": f"retryAfterEpoch={int(retry_at)}",
                }
            )
            continue
        eligible.append(route_id)

    for route_id in eligible[:max_attempts]:
        region, model_id = _split_route(route_id)
        attempted.append(route_id)
        try:
            result = invoke_text(
                route_id,
                prompt,
                client=client,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
            )
            _PREFERRED_MODEL = route_id
            _MODEL_FAILURE_UNTIL.pop(route_id, None)
            return {
                "ok": True,
                "routeId": route_id,
                "region": result.get("region") or region,
                "modelId": result.get("modelId") or model_id,
                "endpointFamily": result.get("endpointFamily")
                or _endpoint_family(route_id),
                "text": result.get("text"),
                "usage": result.get("usage") or {},
                "attemptedModelIds": attempted,
                "errorsBeforeSuccess": errors,
                "discoveredModelCount": discovered_count,
            }
        except Exception as exc:
            response = getattr(exc, "response", {}) or {}
            code = str(
                (response.get("Error") or {}).get("Code") or type(exc).__name__
            )
            message = str(exc)[:480]
            _MODEL_FAILURE_UNTIL[route_id] = now + _failure_cooldown_seconds(
                code, message
            )
            errors.append(
                {
                    "routeId": route_id,
                    "region": region,
                    "modelId": model_id,
                    "endpointFamily": _endpoint_family(route_id),
                    "errorCode": code,
                    "message": message,
                }
            )

    return {
        "ok": False,
        "attemptedModelIds": attempted,
        "errors": errors,
        "discoveredModelCount": discovered_count,
    }
