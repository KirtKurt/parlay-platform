from __future__ import annotations

import json
from typing import Any

import boto3

from . import llm_rd
from . import model_access


def _json_text(text: str) -> dict:
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


def _model_candidates(model_id: str) -> tuple[str, ...]:
    value = str(model_id)
    foundation = value
    for prefix in ('global.', 'us.', 'eu.', 'jp.', 'au.'):
        if foundation.startswith(prefix):
            foundation = foundation[len(prefix):]
            break
    if not foundation.startswith('anthropic.claude-'):
        return (value,)
    candidates = (
        foundation,
        f'global.{foundation}',
        f'us.{foundation}',
        value,
    )
    return tuple(dict.fromkeys(candidates))


def _converse(runtime, model_id: str, prompt: str, max_tokens: int) -> tuple[dict, dict, str]:
    kwargs: dict[str, Any] = {
        'modelId': model_id,
        'messages': [{'role': 'user', 'content': [{'text': prompt}]}],
        'inferenceConfig': {'maxTokens': max(128, min(8000, int(max_tokens)))},
    }
    if 'claude-opus-4-7' in model_id:
        kwargs['additionalModelRequestFields'] = {
            'thinking': {'type': 'adaptive'},
            'output_config': {'effort': 'low'},
        }
    response = dict(runtime.converse(**kwargs) or {})
    content = (((response.get('output') or {}).get('message') or {}).get('content') or [])
    text = ''.join(str(row.get('text') or '') for row in content if isinstance(row, dict))
    usage = dict(response.get('usage') or {})
    usage['resolved_model_id'] = model_id
    usage['endpoint_family'] = 'bedrock-runtime-converse'
    return _json_text(text), usage, model_id


def invoke_with_identifier_fallback(
    configured_model_id: str,
    prompt: str,
    *,
    runtime=None,
    max_tokens: int = 1200,
) -> tuple[dict, dict, str, list[str]]:
    client = runtime or boto3.client('bedrock-runtime')
    errors: list[str] = []
    for candidate in _model_candidates(configured_model_id):
        try:
            payload, usage, resolved = _converse(
                client,
                candidate,
                prompt,
                max_tokens,
            )
            return payload, usage, resolved, errors
        except Exception as exc:
            errors.append(
                f'{candidate}:{type(exc).__name__}:{str(exc)[:800]}'
            )
    raise RuntimeError('ALL_IDENTIFIERS_FAILED|' + '|'.join(errors))


def _patched_llm_invoke(prompt, client=None):
    errors: list[str] = []
    for configured in llm_rd.MODEL_IDS:
        try:
            payload, usage, resolved, identifier_errors = invoke_with_identifier_fallback(
                configured,
                prompt,
                runtime=client,
                max_tokens=llm_rd.MAX_OUTPUT_TOKENS,
            )
            usage['configured_model_id'] = configured
            usage['identifier_errors'] = identifier_errors
            return payload, usage, resolved, errors
        except Exception as exc:
            errors.append(
                f'{configured}:{type(exc).__name__}:{str(exc)[:1000]}'
            )
    raise RuntimeError('ALL_BEDROCK_MODELS_FAILED|' + '|'.join(errors))


def _patched_access_probe(runtime, runtime_model_id: str) -> dict[str, Any]:
    try:
        payload, usage, resolved, identifier_errors = invoke_with_identifier_fallback(
            runtime_model_id,
            'Return exactly this JSON object and nothing else: '
            '{"mlb_auto_opus_access":true}',
            runtime=runtime,
            max_tokens=512,
        )
        confirmed = payload.get('mlb_auto_opus_access') is True
        return {
            'ok': confirmed,
            'response_confirmed': confirmed,
            'usage': usage,
            'configured_model_id': runtime_model_id,
            'resolved_model_id': resolved,
            'identifier_errors': identifier_errors,
        }
    except Exception as exc:
        return {
            'ok': False,
            'error_code': type(exc).__name__,
            'error': str(exc)[:2200],
        }


llm_rd._invoke = _patched_llm_invoke
model_access._invoke_probe = _patched_access_probe
