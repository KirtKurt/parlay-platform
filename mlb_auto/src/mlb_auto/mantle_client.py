from __future__ import annotations

import json
import os
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.request import Request, urlopen

SYSTEM_PROMPT = (
    'You are the isolated MLB Auto research scientist. Return compact JSON only. '
    'Use only supplied point-in-time pregame feature names and approved numeric '
    'operations. Never use outcomes, scores, postgame data, external actions, '
    'executable code, or changes to locking, validation, isolation, or promotion rules.'
)

# Bedrock Runtime inference-profile IDs are not always identical to the model
# value accepted by the Anthropic-compatible Bedrock Mantle endpoint. Keep the
# translation explicit so account configuration and provider routing remain
# independently auditable.
_MANTLE_MODEL_ALIASES = {
    'anthropic.claude-sonnet-4-6': 'anthropic.claude-sonnet-4-6-v1',
}


def _region() -> str:
    return os.getenv('AWS_REGION') or os.getenv('AWS_DEFAULT_REGION') or 'us-east-1'


def _token(provider: Callable[[], str] | None = None) -> str:
    if provider is not None:
        return str(provider())
    from aws_bedrock_token_generator import provide_token
    return str(provide_token())


def _foundation_id(model_id: str) -> str:
    value = str(model_id).strip()
    for prefix in ('global.', 'us.', 'eu.', 'jp.', 'au.'):
        if value.startswith(prefix):
            return value[len(prefix):]
    return value


def _mantle_model_id(model_id: str) -> str:
    foundation_id = _foundation_id(model_id)
    return _MANTLE_MODEL_ALIASES.get(foundation_id, foundation_id)


def _json(text: Any) -> dict:
    raw = str(text or '').strip()
    if raw.startswith('```'):
        raw = raw.strip('`')
        if raw.startswith('json'):
            raw = raw[4:].strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find('{')
        end = raw.rfind('}')
        if start >= 0 and end > start:
            return json.loads(raw[start:end + 1])
        raise


def invoke_anthropic(
    model_id: str,
    prompt: str,
    *,
    max_tokens: int,
    system_prompt: str = SYSTEM_PROMPT,
    token_provider=None,
    post=None,
) -> tuple[dict, dict]:
    foundation_id = _foundation_id(model_id)
    mantle_model_id = _mantle_model_id(model_id)
    payload: dict[str, Any] = {
        'model': mantle_model_id,
        'max_tokens': max(256, min(8000, int(max_tokens))),
        'system': system_prompt,
        'messages': [{'role': 'user', 'content': prompt}],
    }

    timeout = max(30, min(300, int(os.getenv('MLB_AUTO_LLM_HTTP_TIMEOUT_SECONDS', '180'))))
    headers = {
        'x-api-key': _token(token_provider),
        'anthropic-version': '2023-06-01',
        'Content-Type': 'application/json',
    }
    url = f'https://bedrock-mantle.{_region()}.api.aws/anthropic/v1/messages'
    if post is not None:
        response = post(url=url, headers=headers, json=payload, timeout=timeout)
        if hasattr(response, 'raise_for_status'):
            response.raise_for_status()
        body = dict(response.json()) if hasattr(response, 'json') else dict(response)
    else:
        request = Request(
            url,
            data=json.dumps(payload).encode(),
            headers=headers,
            method='POST',
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                body = json.loads(response.read().decode())
        except HTTPError as exc:
            detail = exc.read().decode(errors='replace')
            raise RuntimeError(f'HTTP_{exc.code}:{detail[:1800]}') from exc

    text = ''.join(
        str(item.get('text') or '')
        for item in body.get('content') or []
        if isinstance(item, dict) and item.get('type') == 'text'
    )
    usage = dict(body.get('usage') or {})
    usage.update({
        'endpoint_family': 'bedrock-mantle-anthropic',
        'foundation_model_id': foundation_id,
        'mantle_model_id': mantle_model_id,
        'configured_model_id': str(model_id),
    })
    return _json(text), usage
