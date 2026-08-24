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


# Keep the first four direct in-Region Amazon routes first for deterministic
# recovery and backwards-compatible tests, then move to independent regional
# source pools before retrying the remaining local/profile/model families.
RECOVERY_MODELS = (
    "amazon.nova-micro-v1:0",
    "amazon.nova-lite-v1:0",
    "amazon.nova-pro-v1:0",
    "amazon.nova-2-lite-v1:0",
    "us.amazon.nova-micro-v1:0",
    "us.amazon.nova-lite-v1:0",
    "global.amazon.nova-2-lite-v1:0",
    "us.amazon.nova-2-lite-v1:0",
    "us.meta.llama3-2-1b-instruct-v1:0",
    "us.meta.llama3-2-3b-instruct-v1:0",
    "ai21.jamba-1-5-mini-v1:0",
)

# Bedrock daily invocation-token quotas are enforced by model/profile and
# source Region. A region-qualified spec is internal syntax; only the model ID
# after ``::`` is sent to Bedrock Runtime in the selected Region.
REGIONAL_RECOVERY_MODELS = (
    "us-west-2::us.amazon.nova-micro-v1:0",
    "us-east-2::us.amazon.nova-micro-v1:0",
    "us-west-2::us.amazon.nova-lite-v1:0",
    "us-east-2::us.amazon.nova-lite-v1:0",
    "us-west-2::us.amazon.nova-pro-v1:0",
    "us-east-2::us.amazon.nova-pro-v1:0",
    "us-west-2::us.meta.llama4-scout-17b-instruct-v1:0",
    "us-east-2::us.meta.llama4-scout-17b-instruct-v1:0",
)

DEFAULT_MODELS = RECOVERY_MODELS + (
    "openai.gpt-5.6-sol",
    "anthropic.claude-opus-4-8",
    "anthropic.claude-opus-4-7",
    "anthropic.claude-opus-4-6-v1",
    "anthropic.claude-sonnet-4-6-v1",
    "us.amazon.nova-pro-v1:0",
)

_PREFERRED_MODEL: Optional[str] = None
_MODEL_FAILURE_UNTIL: Dict[str, float] = {}
_RUNTIME_CLIENT: Any = None
_REGIONAL_RUNTIME_CLIENTS: Dict[str, Any] = {}
_CONTROL_CLIENT: Any = None
_REGION_PATTERN = re.compile(r"^[a-z]{2}(?:-gov)?-[a-z]+-\d+$")

# Fail over quickly instead of letting one throttled model consume the whole
# Lambda timeout through SDK retries.
_BOTO_CONFIG = Config(
    connect_timeout=5,
    read_timeout=35,
    retries={"max_attempts": 1, "mode": "standard"},
)


def _region() -> str:
    return (
        os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or "us-east-1"
    )


def _split_model_spec(model_spec: Any) -> Tuple[str, str]:
    value = str(model_spec or "").strip()
    if not value:
        raise ValueError("BEDROCK_MODEL_SPEC_EMPTY")
    if "::" not in value:
        return _region(), value
    region, model_id = (part.strip() for part in value.split("::", 1))
    if not _REGION_PATTERN.fullmatch(region) or not model_id:
        raise ValueError("BEDROCK_MODEL_SPEC_INVALID")
    return region, model_id


def _runtime_client(region: Optional[str] = None) -> Any:
    global _RUNTIME_CLIENT
    requested = str(region or _region())
    if requested == _region():
        if _RUNTIME_CLIENT is None:
            _RUNTIME_CLIENT = boto3.client(
                "bedrock-runtime", region_name=requested, config=_BOTO_CONFIG
            )
        return _RUNTIME_CLIENT
    if requested not in _REGIONAL_RUNTIME_CLIENTS:
        _REGIONAL_RUNTIME_CLIENTS[requested] = boto3.client(
            "bedrock-runtime", region_name=requested, config=_BOTO_CONFIG
        )
    return _REGIONAL_RUNTIME_CLIENTS[requested]


def _control_client() -> Any:
    global _CONTROL_CLIENT
    if _CONTROL_CLIENT is None:
        _CONTROL_CLIENT = boto3.client(
            "bedrock", region_name=_region(), config=_BOTO_CONFIG
        )
    return _CONTROL_CLIENT


def _dedupe(values: Iterable[str]) -> List[str]:
    output: List[str] = []
    seen = set()
    for raw in values:
        value = str(raw or "").strip()
        if value and value not in seen:
            seen.add(value)
            output.append(value)
    return output


def _model_priority(model_id: str) -> tuple[int, int, str]:
    try:
        _, actual_model_id = _split_model_spec(model_id)
    except ValueError:
        actual_model_id = str(model_id or "")
    value = actual_model_id.lower()
    if value.startswith("amazon.nova-micro"):
        return (0, 0, value)
    if value.startswith("amazon.nova-lite"):
        return (0, 1, value)
    if value.startswith("amazon.nova-2-lite"):
        return (0, 2, value)
    if value.startswith("amazon."):
        return (0, 3, value)
    if any(
        token in value
        for token in ("micro", "mini", "lite", "small", "1b", "3b", "8b", "haiku")
    ):
        return (1, 0, value)
    if value.startswith(("us.", "global.")):
        return (2, 0, value)
    if value.startswith(("openai.", "anthropic.")):
        return (4, 0, value)
    return (3, 0, value)


@lru_cache(maxsize=1)
def discovered_models() -> List[str]:
    """List current account-visible text models and active inference profiles.

    Discovery is best-effort. Static recovery candidates remain available when
    the Bedrock control-plane call is temporarily unavailable or not authorized.
    """

    if os.environ.get("MLB_AUTO_BEDROCK_DISCOVERY", "true").lower() in {
        "0",
        "false",
        "no",
    }:
        return []

    candidates: List[str] = []
    client = _control_client()

    try:
        response = client.list_inference_profiles(
            typeEquals="SYSTEM_DEFINED", maxResults=1000
        )
        for row in response.get("inferenceProfileSummaries") or []:
            if not isinstance(row, dict) or row.get("status") != "ACTIVE":
                continue
            model_id = str(row.get("inferenceProfileId") or "").strip()
            if model_id:
                candidates.append(model_id)
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
                candidates.append(model_id)
    except Exception:
        pass

    return sorted(_dedupe(candidates), key=_model_priority)


def configured_models() -> List[str]:
    configured = [
        value.strip()
        for value in os.environ.get(
            "MLB_AUTO_BEDROCK_MODELS", ",".join(DEFAULT_MODELS)
        ).split(",")
        if value.strip()
    ]
    return _dedupe(
        (
            *RECOVERY_MODELS[:4],
            *REGIONAL_RECOVERY_MODELS,
            *RECOVERY_MODELS[4:],
            *discovered_models(),
            *configured,
        )
    )


def _endpoint_family(model_id: str) -> str:
    try:
        _, actual_model_id = _split_model_spec(model_id)
    except ValueError:
        actual_model_id = str(model_id or "")
    if actual_model_id.startswith("openai."):
        return "bedrock-mantle-openai"
    if actual_model_id.startswith("anthropic."):
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
    timeout: int = 90,
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


def _invoke_openai(model_id: str, prompt: str, *, max_tokens: int) -> Dict[str, Any]:
    payload = _post_json(
        f"https://bedrock-mantle.{_region()}.api.aws/openai/v1/responses",
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
    }


def _invoke_anthropic(
    model_id: str,
    prompt: str,
    *,
    max_tokens: int,
    temperature: float,
) -> Dict[str, Any]:
    payload = _post_json(
        f"https://bedrock-mantle.{_region()}.api.aws/anthropic/v1/messages",
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
    }


def invoke_text(
    model_id: str,
    prompt: str,
    *,
    client: Any = None,
    max_tokens: int = 900,
    temperature: float = 0.0,
    top_p: float = 0.9,
) -> Dict[str, Any]:
    region, actual_model_id = _split_model_spec(model_id)
    if actual_model_id.startswith("openai."):
        return _invoke_openai(actual_model_id, prompt, max_tokens=max_tokens)
    if actual_model_id.startswith("anthropic."):
        return _invoke_anthropic(
            actual_model_id,
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    runtime = client or _runtime_client(region)
    response = runtime.converse(
        modelId=actual_model_id,
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
        "modelRegion": region,
        "actualModelId": actual_model_id,
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
    errors: List[Dict[str, str]] = []
    max_attempts = max(
        1, int(os.environ.get("MLB_AUTO_BEDROCK_MAX_MODEL_ATTEMPTS", "20"))
    )

    eligible: List[str] = []
    for model_id in _ordered_models(models or configured_models()):
        retry_at = float(_MODEL_FAILURE_UNTIL.get(model_id) or 0.0)
        if retry_at > now:
            errors.append(
                {
                    "modelId": model_id,
                    "endpointFamily": _endpoint_family(model_id),
                    "errorCode": "MODEL_COOLDOWN",
                    "message": f"retryAfterEpoch={int(retry_at)}",
                }
            )
            continue
        eligible.append(model_id)

    for model_id in eligible[:max_attempts]:
        attempted.append(model_id)
        try:
            result = invoke_text(
                model_id,
                prompt,
                client=client,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
            )
            _PREFERRED_MODEL = model_id
            _MODEL_FAILURE_UNTIL.pop(model_id, None)
            return {
                "ok": True,
                "modelId": model_id,
                "modelRegion": result.get("modelRegion"),
                "actualModelId": result.get("actualModelId") or model_id,
                "endpointFamily": result.get("endpointFamily")
                or _endpoint_family(model_id),
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
            _MODEL_FAILURE_UNTIL[model_id] = now + _failure_cooldown_seconds(
                code, message
            )
            errors.append(
                {
                    "modelId": model_id,
                    "endpointFamily": _endpoint_family(model_id),
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
