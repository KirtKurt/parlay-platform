from __future__ import annotations

import json
import os
from typing import Any, Dict, Iterable, List, Optional
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import boto3


DEFAULT_MODELS = (
    "openai.gpt-5.6-sol",
    "anthropic.claude-opus-4-8",
    "anthropic.claude-opus-4-7",
    "anthropic.claude-opus-4-6-v1",
    "anthropic.claude-sonnet-4-6-v1",
    "us.amazon.nova-2-lite-v1:0",
    "global.amazon.nova-2-lite-v1:0",
    "us.amazon.nova-pro-v1:0",
    "us.amazon.nova-lite-v1:0",
    "us.amazon.nova-micro-v1:0",
)

_PREFERRED_MODEL: Optional[str] = None


def configured_models() -> List[str]:
    return [
        value.strip()
        for value in os.environ.get(
            "MLB_AUTO_BEDROCK_MODELS", ",".join(DEFAULT_MODELS)
        ).split(",")
        if value.strip()
    ]


def _region() -> str:
    return (
        os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or "us-east-1"
    )


def _endpoint_family(model_id: str) -> str:
    if model_id.startswith("openai."):
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


def _post_json(url: str, headers: Dict[str, str], payload: Dict[str, Any], timeout: int = 120) -> Dict[str, Any]:
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
            "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
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


def _invoke_anthropic(model_id: str, prompt: str, *, max_tokens: int, temperature: float) -> Dict[str, Any]:
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
    if model_id.startswith("openai."):
        return _invoke_openai(model_id, prompt, max_tokens=max_tokens)
    if model_id.startswith("anthropic."):
        return _invoke_anthropic(
            model_id,
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    runtime = client or boto3.client("bedrock-runtime")
    response = runtime.converse(
        modelId=model_id,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={
            "maxTokens": int(max_tokens),
            "temperature": float(temperature),
            "topP": float(top_p),
        },
    )
    blocks = (((response.get("output") or {}).get("message") or {}).get("content") or [])
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
    }


def _ordered_models(models: Iterable[str]) -> List[str]:
    values = [str(value).strip() for value in models if str(value).strip()]
    if _PREFERRED_MODEL and _PREFERRED_MODEL in values:
        return [_PREFERRED_MODEL] + [value for value in values if value != _PREFERRED_MODEL]
    return values


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

    attempted: List[str] = []
    errors: List[Dict[str, str]] = []
    for model_id in _ordered_models(models or configured_models()):
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
            return {
                "ok": True,
                "modelId": model_id,
                "endpointFamily": result.get("endpointFamily") or _endpoint_family(model_id),
                "text": result.get("text"),
                "usage": result.get("usage") or {},
                "attemptedModelIds": attempted,
                "errorsBeforeSuccess": errors,
            }
        except Exception as exc:
            response = getattr(exc, "response", {}) or {}
            code = str((response.get("Error") or {}).get("Code") or type(exc).__name__)
            errors.append(
                {
                    "modelId": model_id,
                    "endpointFamily": _endpoint_family(model_id),
                    "errorCode": code,
                    "message": str(exc)[:480],
                }
            )

    return {
        "ok": False,
        "attemptedModelIds": attempted,
        "errors": errors,
    }
