from __future__ import annotations

import json
import os
import time
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.error import HTTPError
from urllib.request import Request, urlopen

try:
    import model_gateway as legacy
except ModuleNotFoundError:  # pragma: no cover - package import in unit tests
    from mlb_auto_llm import model_gateway as legacy


MANTLE_ROUTE_PREFIX = "mantle::"
_ROUTE_SEPARATOR = "::"

_PREFERRED_ROUTE: Optional[str] = None
_ROUTE_FAILURE_UNTIL: Dict[str, float] = {}


def _dedupe(values: Iterable[str]) -> List[str]:
    output: List[str] = []
    seen = set()
    for raw in values:
        value = str(raw or "").strip()
        if value and value not in seen:
            seen.add(value)
            output.append(value)
    return output


def _mantle_route(region: str, model_id: str) -> str:
    return f"{MANTLE_ROUTE_PREFIX}{region}{_ROUTE_SEPARATOR}{model_id}"


def _split_mantle_route(route_id: str) -> Optional[Tuple[str, str]]:
    value = str(route_id or "").strip()
    if not value.startswith(MANTLE_ROUTE_PREFIX):
        return None
    remainder = value[len(MANTLE_ROUTE_PREFIX) :]
    if _ROUTE_SEPARATOR not in remainder:
        return None
    region, model_id = remainder.split(_ROUTE_SEPARATOR, 1)
    return (region, model_id) if region and model_id else None


def _http_json(
    url: str,
    *,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    payload: Optional[Dict[str, Any]] = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={"accept": "application/json", **(headers or {})},
        method=method,
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            parsed = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP_{exc.code}:{detail[:1200]}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("BEDROCK_ENDPOINT_RESPONSE_NOT_OBJECT")
    return parsed


def _model_id_from_row(row: Any) -> str:
    if not isinstance(row, dict):
        return ""
    return str(row.get("id") or row.get("model") or row.get("modelId") or "").strip()


def _is_text_candidate(model_id: str) -> bool:
    value = str(model_id or "").lower()
    if not value:
        return False
    return not any(
        token in value
        for token in (
            "embed",
            "rerank",
            "image",
            "vision",
            "video",
            "audio",
            "speech",
            "voxtral",
            "guardrail",
            "safeguard",
            "moderation",
            "canvas",
        )
    )


def _runtime_provider(model_id: str) -> str:
    parts = str(model_id or "").split(".")
    if parts and parts[0] in {"us", "global", "eu", "apac"}:
        parts = parts[1:]
    return parts[0].lower() if parts else ""


def _runtime_allowed(model_id: str) -> bool:
    value = str(model_id or "").lower()
    if not _is_text_candidate(value):
        return False
    providers = {
        item.strip().lower()
        for item in os.environ.get(
            "MLB_AUTO_BEDROCK_RUNTIME_PROVIDERS", "amazon,openai"
        ).split(",")
        if item.strip()
    }
    provider = _runtime_provider(value)
    if provider not in providers:
        return False
    # GPT-5.x uses an OpenAI-compatible Responses route. The Runtime route
    # retained here is only the open-weight gpt-oss family supported by the
    # existing Converse adapter.
    if provider == "openai" and "gpt-oss" not in value:
        return False
    return True


def _route_priority(route_id: str) -> Tuple[int, int, str]:
    mantle = _split_mantle_route(route_id)
    model_id = mantle[1] if mantle else legacy._split_route(route_id)[1]
    value = model_id.lower()
    small = any(
        token in value
        for token in (
            "luna",
            "mini",
            "micro",
            "lite",
            "small",
            "flash",
            "nano",
            "haiku",
            "20b",
            "7b",
            "8b",
            "9b",
            "12b",
        )
    )
    if mantle:
        return (0 if small else 1, len(value), route_id)
    if "gpt-oss-20b" in value:
        return (2, len(value), route_id)
    if value.startswith(("amazon.nova-micro", "amazon.nova-lite")):
        return (3, len(value), route_id)
    if value.startswith("amazon.titan-text-"):
        return (4, len(value), route_id)
    if "gpt-oss-120b" in value:
        return (5, len(value), route_id)
    if value.startswith("amazon.nova"):
        return (6, len(value), route_id)
    return (7, len(value), route_id)


@lru_cache(maxsize=1)
def mantle_models() -> List[str]:
    if os.environ.get("MLB_AUTO_BEDROCK_MANTLE_DISCOVERY", "true").lower() in {
        "0",
        "false",
        "no",
    }:
        return []
    try:
        token = legacy._bearer_token()
    except Exception:
        # Runtime discovery and invocation remain available if API-key
        # generation for Mantle is unavailable in the current role/account.
        return []
    routes: List[str] = []
    for region in legacy.configured_regions():
        try:
            payload = _http_json(
                f"https://bedrock-mantle.{region}.api.aws/v1/models",
                headers={"Authorization": f"Bearer {token}"},
                timeout=20,
            )
        except Exception:
            continue
        rows = payload.get("data") or payload.get("models") or []
        for row in rows:
            model_id = _model_id_from_row(row)
            if _is_text_candidate(model_id):
                routes.append(_mantle_route(region, model_id))
    return sorted(_dedupe(routes), key=_route_priority)


def _runtime_region_models(region: str) -> List[str]:
    routes: List[str] = []
    client = legacy._control_client(region)
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
            if _runtime_allowed(model_id):
                routes.append(legacy._format_route(region, model_id))
    except Exception:
        pass
    try:
        response = client.list_inference_profiles(
            typeEquals="SYSTEM_DEFINED", maxResults=1000
        )
        for row in response.get("inferenceProfileSummaries") or []:
            if not isinstance(row, dict) or row.get("status") != "ACTIVE":
                continue
            model_id = str(row.get("inferenceProfileId") or "").strip()
            if _runtime_allowed(model_id):
                routes.append(legacy._format_route(region, model_id))
    except Exception:
        pass
    return routes


@lru_cache(maxsize=1)
def runtime_models() -> List[str]:
    if os.environ.get("MLB_AUTO_BEDROCK_RUNTIME_DISCOVERY", "true").lower() in {
        "0",
        "false",
        "no",
    }:
        return []
    routes: List[str] = []
    for region in legacy.configured_regions():
        routes.extend(_runtime_region_models(region))
    return sorted(_dedupe(routes), key=_route_priority)


def configured_models() -> List[str]:
    explicit = [
        value.strip()
        for value in os.environ.get("MLB_AUTO_BEDROCK_MODELS", "").split(",")
        if value.strip()
    ]
    discovered_runtime = runtime_models()
    discovered_ids = {
        (region, model_id)
        for region, model_id in (legacy._split_route(route) for route in discovered_runtime)
    }
    validated_explicit: List[str] = []
    for route in explicit:
        if _split_mantle_route(route):
            validated_explicit.append(route)
            continue
        region, model_id = legacy._split_route(route)
        if _runtime_allowed(model_id) and (
            not discovered_runtime or (region, model_id) in discovered_ids
        ):
            validated_explicit.append(route)
    routes = _dedupe((*mantle_models(), *validated_explicit, *discovered_runtime))
    limit = max(1, int(os.environ.get("MLB_AUTO_BEDROCK_ROUTE_CATALOG_LIMIT", "64")))
    return sorted(routes, key=_route_priority)[:limit]


def _openai_text(payload: Dict[str, Any]) -> str:
    if payload.get("output_text"):
        return str(payload.get("output_text") or "").strip()
    parts: List[str] = []
    for item in payload.get("output") or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content") or []:
            if isinstance(content, dict) and content.get("type") in {
                "output_text",
                "text",
            }:
                parts.append(str(content.get("text") or ""))
    return "".join(parts).strip()


def _invoke_mantle(
    region: str,
    model_id: str,
    prompt: str,
    *,
    max_tokens: int,
    temperature: float,
) -> Dict[str, Any]:
    payload = _http_json(
        f"https://bedrock-mantle.{region}.api.aws/v1/responses",
        method="POST",
        headers={
            "Authorization": f"Bearer {legacy._bearer_token()}",
            "Content-Type": "application/json",
        },
        payload={
            "model": model_id,
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": prompt}],
                }
            ],
            "max_output_tokens": int(max_tokens),
            "temperature": float(temperature),
            "store": False,
        },
        timeout=75,
    )
    text = _openai_text(payload)
    if not text:
        raise RuntimeError("EMPTY_BEDROCK_MANTLE_RESPONSE")
    return {
        "text": text,
        "usage": dict(payload.get("usage") or {}),
        "endpointFamily": "bedrock-mantle-responses",
        "region": region,
        "modelId": model_id,
    }


def _invoke_titan(
    runtime: Any,
    model_id: str,
    prompt: str,
    *,
    max_tokens: int,
    temperature: float,
    top_p: float,
) -> Dict[str, Any]:
    request = {
        "inputText": prompt,
        "textGenerationConfig": {
            "maxTokenCount": int(max_tokens),
            "temperature": float(temperature),
            "topP": float(top_p),
            "stopSequences": [],
        },
    }
    response = runtime.invoke_model(
        modelId=model_id,
        contentType="application/json",
        accept="application/json",
        body=json.dumps(request).encode("utf-8"),
    )
    stream = response.get("body")
    raw = stream.read() if hasattr(stream, "read") else stream
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    payload = json.loads(str(raw or "{}"))
    results = payload.get("results") or []
    row = results[0] if results and isinstance(results[0], dict) else {}
    text = str(row.get("outputText") or "").strip()
    if not text:
        raise RuntimeError("EMPTY_BEDROCK_TITAN_TEXT_RESPONSE")
    return {
        "text": text,
        "usage": {
            "inputTokens": payload.get("inputTextTokenCount"),
            "outputTokens": row.get("tokenCount"),
        },
        "endpointFamily": "bedrock-runtime-invoke-model-titan-text",
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
    mantle = _split_mantle_route(route_id)
    if mantle:
        region, model_id = mantle
        return _invoke_mantle(
            region,
            model_id,
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
        )
    region, model_id = legacy._split_route(route_id)
    runtime = client or legacy._runtime_client(region)
    if model_id.startswith("amazon.titan-text-"):
        result = _invoke_titan(
            runtime,
            model_id,
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
        )
        return {**result, "region": region, "modelId": model_id}
    return legacy.invoke_text(
        route_id,
        prompt,
        client=client,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
    )


def _failure_cooldown_seconds(code: str, message: str) -> int:
    normalized = f"{code} {message}".lower()
    if "too many tokens per day" in normalized:
        return 21600
    if any(
        token in normalized
        for token in (
            "invalid_payment_instrument",
            "not available for this account",
            "accessdenied",
            "access denied",
            "does not exist",
            "resource not found",
            "unsupported model",
            "end of its life",
            "validationexception",
        )
    ):
        return 86400
    if "throttl" in normalized or "too many tokens" in normalized:
        return 300
    return 60


def _ordered_routes(routes: Iterable[str]) -> List[str]:
    values = _dedupe(routes)
    if _PREFERRED_ROUTE and _PREFERRED_ROUTE in values:
        return [_PREFERRED_ROUTE] + [value for value in values if value != _PREFERRED_ROUTE]
    return values


def invoke_chain_text(
    prompt: str,
    models: Optional[Iterable[str]] = None,
    *,
    client: Any = None,
    max_tokens: int = 900,
    temperature: float = 0.0,
    top_p: float = 0.9,
    max_attempts: Optional[int] = None,
) -> Dict[str, Any]:
    global _PREFERRED_ROUTE

    catalog = _ordered_routes(
        configured_models() if models is None else models
    )
    now = time.time()
    configured_max_attempts = (
        max_attempts
        if max_attempts is not None
        else int(os.environ.get("MLB_AUTO_BEDROCK_MAX_MODEL_ATTEMPTS", "8"))
    )
    max_attempts = max(1, int(configured_max_attempts))
    attempted: List[str] = []
    errors: List[Dict[str, Any]] = []
    for route_id in catalog:
        retry_at = float(_ROUTE_FAILURE_UNTIL.get(route_id) or 0.0)
        if retry_at > now:
            errors.append(
                {
                    "routeId": route_id,
                    "errorCode": "MODEL_COOLDOWN",
                    "message": f"retryAfterEpoch={int(retry_at)}",
                }
            )
            continue
        if len(attempted) >= max_attempts:
            break
        attempted.append(route_id)
        mantle = _split_mantle_route(route_id)
        if mantle:
            region, model_id = mantle
            endpoint = "bedrock-mantle-responses"
        else:
            region, model_id = legacy._split_route(route_id)
            endpoint = (
                "bedrock-runtime-invoke-model-titan-text"
                if model_id.startswith("amazon.titan-text-")
                else "bedrock-runtime-converse"
            )
        try:
            result = invoke_text(
                route_id,
                prompt,
                client=client,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
            )
            _PREFERRED_ROUTE = route_id
            _ROUTE_FAILURE_UNTIL.pop(route_id, None)
            return {
                "ok": True,
                "routeId": route_id,
                "region": result.get("region") or region,
                "modelId": result.get("modelId") or model_id,
                "endpointFamily": result.get("endpointFamily") or endpoint,
                "text": result.get("text"),
                "usage": result.get("usage") or {},
                "attemptedModelIds": attempted,
                "errorsBeforeSuccess": errors,
                "mantleModelCount": len(mantle_models()),
                "runtimeModelCount": len(runtime_models()),
            }
        except Exception as exc:
            response = getattr(exc, "response", {}) or {}
            code = str((response.get("Error") or {}).get("Code") or type(exc).__name__)
            message = str(exc)[:480]
            _ROUTE_FAILURE_UNTIL[route_id] = now + _failure_cooldown_seconds(code, message)
            errors.append(
                {
                    "routeId": route_id,
                    "region": region,
                    "modelId": model_id,
                    "endpointFamily": endpoint,
                    "errorCode": code,
                    "message": message,
                }
            )
    return {
        "ok": False,
        "attemptedModelIds": attempted,
        "errors": errors,
        "mantleModelCount": len(mantle_models()),
        "runtimeModelCount": len(runtime_models()),
    }


def reset_model_state(
    *, clear_discovery: bool = False, clear_failures: bool = False
) -> None:
    global _PREFERRED_ROUTE
    _PREFERRED_ROUTE = None
    if clear_failures:
        _ROUTE_FAILURE_UNTIL.clear()
    if clear_discovery:
        mantle_models.cache_clear()
        runtime_models.cache_clear()
