from __future__ import annotations

import json
import os
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from . import llm_rd
from . import model_access

SYSTEM_PROMPT = (
    'You are the isolated MLB Auto research scientist. Return compact JSON only. '
    'Use only supplied point-in-time pregame feature names and approved numeric '
    'operations. Never use outcomes, scores, postgame data, external actions, '
    'executable code, or changes to locking, validation, isolation, or promotion rules.'
)


def _region() -> str:
    return os.getenv('AWS_REGION') or os.getenv('AWS_DEFAULT_REGION') or 'us-east-1'


def _token(provider: Callable[[], str] | None = None) -> str:
    if provider is not None:
        return str(provider())
    from aws_bedrock_token_generator import provide_token
    return str(provide_token())


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


def _post(
    *,
    url: str,
    headers: dict[str, str],
    payload: dict,
    post: Callable[..., Any] | None = None,
) -> dict:
    timeout = max(30, min(300, int(os.getenv('MLB_AUTO_LLM_HTTP_TIMEOUT_SECONDS', '180'))))
    if post is not None:
        response = post(url=url, headers=headers, json=payload, timeout=timeout)
        if hasattr(response, 'raise_for_status'):
            response.raise_for_status()
        if hasattr(response, 'json'):
            return dict(response.json())
        return dict(response)
    request = Request(
        url,
        data=json.dumps(payload).encode(),
        headers=headers,
        method='POST',
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except HTTPError as exc:
        body = exc.read().decode(errors='replace')
        raise RuntimeError(f'HTTP_{exc.code}:{body[:1800]}') from exc


def _foundation_id(model_id: str) -> str:
    value = str(model_id)
    for prefix in ('global.', 'us.', 'eu.', 'jp.', 'au.'):
        if value.startswith(prefix):
            return value[len(prefix):]
    return value


def invoke_anthropic_mantle(
    model_id: str,
    prompt: str,
    *,
    max_tokens: int,
    token_provider=None,
    post=None,
) -> tuple[dict, dict]:
    foundation_id = _foundation_id(model_id)
    request_payload: dict[str, Any] = {
        'model': foundation_id,
        'max_tokens': max(256, min(8000, int(max_tokens))),
        'system': SYSTEM_PROMPT,
        'messages': [{'role': 'user', 'content': prompt}],
    }
    if foundation_id == 'anthropic.claude-opus-4-7':
        request_payload['thinking'] = {'type': 'adaptive'}
        request_payload['output_config'] = {'effort': 'low'}
    response = _post(
        url=f'https://bedrock-mantle.{_region()}.api.aws/anthropic/v1/messages',
        headers={
            'x-api-key': _token(token_provider),
            'anthropic-version': '2023-06-01',
            'Content-Type': 'application/json',
        },
        payload=request_payload,
        post=post,
    )
    text = ''.join(
        str(item.get('text') or '')
        for item in response.get('content') or []
        if isinstance(item, dict) and item.get('type') == 'text'
    )
    usage = dict(response.get('usage') or {})
    usage.update({
        'endpoint_family': 'bedrock-mantle-anthropic',
        'foundation_model_id': foundation_id,
        'configured_model_id': str(model_id),
    })
    return _json(text), usage


def _patched_llm_invoke(prompt, client=None):
    errors: list[str] = []
    for model_id in llm_rd.MODEL_IDS:
        try:
            if 'anthropic.claude-' in str(model_id):
                proposal, usage = invoke_anthropic_mantle(
                    str(model_id),
                    prompt,
                    max_tokens=llm_rd.MAX_OUTPUT_TOKENS,
                )
                return proposal, usage, str(model_id), errors
            response = client.converse(
                modelId=str(model_id),
                system=[{'text': SYSTEM_PROMPT}],
                messages=[{'role': 'user', 'content': [{'text': prompt}]}],
                inferenceConfig={'maxTokens': llm_rd.MAX_OUTPUT_TOKENS},
            ) if client is not None else None
            if response is None:
                import boto3
                response = boto3.client('bedrock-runtime').converse(
                    modelId=str(model_id),
                    system=[{'text': SYSTEM_PROMPT}],
                    messages=[{'role': 'user', 'content': [{'text': prompt}]}],
                    inferenceConfig={'maxTokens': llm_rd.MAX_OUTPUT_TOKENS},
                )
            content = (((response.get('output') or {}).get('message') or {}).get('content') or [])
            text = ''.join(str(item.get('text') or '') for item in content if isinstance(item, dict))
            usage = dict(response.get('usage') or {})
            usage['endpoint_family'] = 'bedrock-runtime-converse'
            return _json(text), usage, str(model_id), errors
        except Exception as exc:
            errors.append(f'{model_id}:{type(exc).__name__}:{str(exc)[:1000]}')
    raise RuntimeError('ALL_LLM_ENDPOINTS_FAILED|' + '|'.join(errors))


def _patched_probe(runtime, runtime_model_id: str) -> dict[str, Any]:
    try:
        payload, usage = invoke_anthropic_mantle(
            runtime_model_id,
            'Return exactly this JSON object and nothing else: '
            '{"mlb_auto_opus_access":true}',
            max_tokens=512,
        )
        confirmed = payload.get('mlb_auto_opus_access') is True
        return {
            'ok': confirmed,
            'response_confirmed': confirmed,
            'usage': usage,
            'endpoint_family': 'bedrock-mantle-anthropic',
            'configured_model_id': runtime_model_id,
            'resolved_model_id': usage.get('foundation_model_id'),
        }
    except Exception as exc:
        return {
            'ok': False,
            'error_code': type(exc).__name__,
            'error': str(exc)[:2200],
            'endpoint_family': 'bedrock-mantle-anthropic',
        }


llm_rd._invoke = _patched_llm_invoke
model_access._invoke_probe = _patched_probe
