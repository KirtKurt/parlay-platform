from __future__ import annotations

import json
import os
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import boto3

SYSTEM = (
    'MLB Auto R&D only. Return compact JSON only. Use only supplied point-in-time '
    'pregame feature names and allowed numeric operations. Never use outcomes, scores, '
    'postgame data, external actions, executable code, or changes to locking, validation, '
    'isolation, or promotion rules.'
)


def _region() -> str:
    return os.getenv('AWS_REGION') or os.getenv('AWS_DEFAULT_REGION') or 'us-east-1'


def _token(provider: Callable[[], str] | None = None) -> str:
    if provider is not None:
        return str(provider())
    from aws_bedrock_token_generator import provide_token
    return str(provide_token())


def _decode_json(text: Any) -> dict:
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
    timeout: int = 180,
) -> dict:
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
        raise RuntimeError(f'HTTP_{exc.code}:{body[:1600]}') from exc


def invoke_anthropic(
    model_id: str,
    prompt: str,
    *,
    max_tokens: int = 1200,
    token_provider=None,
    post=None,
) -> tuple[dict, dict]:
    foundation_id = str(model_id)
    for prefix in ('global.', 'us.', 'eu.', 'jp.', 'au.'):
        if foundation_id.startswith(prefix):
            foundation_id = foundation_id[len(prefix):]
            break
    payload: dict[str, Any] = {
        'model': foundation_id,
        'max_tokens': max(256, min(8000, int(max_tokens))),
        'system': SYSTEM,
        'messages': [{'role': 'user', 'content': prompt}],
    }
    if foundation_id.endswith('claude-opus-4-7'):
        payload['thinking'] = {'type': 'adaptive'}
        payload['output_config'] = {'effort': 'low'}
    response = _post(
        url=f'https://bedrock-mantle.{_region()}.api.aws/anthropic/v1/messages',
        headers={
            'x-api-key': _token(token_provider),
            'anthropic-version': '2023-06-01',
            'Content-Type': 'application/json',
        },
        payload=payload,
        post=post,
    )
    text = ''.join(
        str(item.get('text') or '')
        for item in response.get('content') or []
        if isinstance(item, dict) and item.get('type') == 'text'
    )
    usage = dict(response.get('usage') or {})
    usage['endpoint_family'] = 'bedrock-mantle-anthropic'
    usage['foundation_model_id'] = foundation_id
    return _decode_json(text), usage


def invoke_converse(
    model_id: str,
    prompt: str,
    *,
    max_tokens: int = 1200,
    client=None,
) -> tuple[dict, dict]:
    runtime = client or boto3.client('bedrock-runtime')
    kwargs: dict[str, Any] = {
        'modelId': str(model_id),
        'system': [{'text': SYSTEM}],
        'messages': [{'role': 'user', 'content': [{'text': prompt}]}],
        'inferenceConfig': {'maxTokens': max(256, min(8000, int(max_tokens)))},
    }
    if str(model_id).endswith('claude-opus-4-7'):
        kwargs['additionalModelRequestFields'] = {
            'thinking': {'type': 'adaptive'},
            'output_config': {'effort': 'low'},
        }
    response = runtime.converse(**kwargs)
    content = (((response.get('output') or {}).get('message') or {}).get('content') or [])
    text = ''.join(str(item.get('text') or '') for item in content if isinstance(item, dict))
    usage = dict(response.get('usage') or {})
    usage['endpoint_family'] = 'bedrock-runtime-converse'
    return _decode_json(text), usage


def invoke(
    model_id: str,
    prompt: str,
    *,
    max_tokens: int = 1200,
    runtime_client=None,
    token_provider=None,
    post=None,
) -> tuple[dict, dict]:
    value = str(model_id)
    if 'anthropic.claude-' in value:
        try:
            return invoke_anthropic(
                value,
                prompt,
                max_tokens=max_tokens,
                token_provider=token_provider,
                post=post,
            )
        except Exception as mantle_error:
            try:
                payload, usage = invoke_converse(
                    value,
                    prompt,
                    max_tokens=max_tokens,
                    client=runtime_client,
                )
                usage['mantle_error'] = f'{type(mantle_error).__name__}:{str(mantle_error)[:600]}'
                return payload, usage
            except Exception as runtime_error:
                raise RuntimeError(
                    'MANTLE_AND_RUNTIME_FAILED|'
                    f'mantle={type(mantle_error).__name__}:{str(mantle_error)[:700]}|'
                    f'runtime={type(runtime_error).__name__}:{str(runtime_error)[:700]}'
                ) from runtime_error
    return invoke_converse(
        value,
        prompt,
        max_tokens=max_tokens,
        client=runtime_client,
    )
