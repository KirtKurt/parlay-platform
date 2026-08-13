from __future__ import annotations

import json
import os
from typing import Any, Callable, Mapping

MODEL_ID = os.getenv('MLB_AUTO_MANTLE_MODEL_ID', 'openai.gpt-5.6-sol')
MAX_OUTPUT_TOKENS = int(os.getenv('MLB_AUTO_MANTLE_MAX_OUTPUT_TOKENS', '2200'))
SYSTEM_PROMPT = (
    'You are the isolated MLB Auto R&D researcher. Return compact JSON only. '
    'Use only supplied pregame feature names and allowed operations. Never use '
    'outcomes, scores, postgame data, external actions, executable code, or '
    'changes to validation rules.'
)


def endpoint() -> str:
    region = os.getenv('AWS_REGION') or os.getenv('AWS_DEFAULT_REGION') or 'us-east-1'
    return f'https://bedrock-mantle.{region}.api.aws/openai/v1/responses'


def _extract_json(text: str):
    raw = str(text or '').strip()
    if raw.startswith('```'):
        lines = raw.splitlines()
        if lines and lines[0].strip().lower() in ('```', '```json'):
            lines = lines[1:]
        if lines and lines[-1].strip() == '```':
            lines = lines[:-1]
        raw = '\n'.join(lines).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find('{'), raw.rfind('}')
        if start >= 0 and end > start:
            return json.loads(raw[start:end + 1])
        raise


def _output_text(payload: Mapping[str, Any]) -> str:
    direct = payload.get('output_text')
    if isinstance(direct, str) and direct.strip():
        return direct
    chunks = []
    for item in payload.get('output') or []:
        if not isinstance(item, Mapping):
            continue
        content = item.get('content') or []
        if isinstance(content, str):
            chunks.append(content)
            continue
        for part in content:
            if isinstance(part, Mapping) and isinstance(part.get('text'), str):
                chunks.append(part['text'])
    if not chunks:
        raise ValueError('MANTLE_RESPONSE_MISSING_OUTPUT_TEXT')
    return ''.join(chunks)


def invoke(
    prompt: str,
    *,
    token_provider: Callable[[], str] | None = None,
    http_post: Callable[..., Any] | None = None,
):
    if token_provider is None:
        from aws_bedrock_token_generator import provide_token
        token_provider = provide_token
    if http_post is None:
        import requests
        http_post = requests.post

    token = str(token_provider() or '')
    if not token:
        raise ValueError('MANTLE_SHORT_TERM_TOKEN_EMPTY')
    url = endpoint()
    body = {
        'model': MODEL_ID,
        'store': False,
        'max_output_tokens': max(500, min(6000, MAX_OUTPUT_TOKENS)),
        'input': [
            {
                'role': 'developer',
                'content': [{'type': 'input_text', 'text': SYSTEM_PROMPT}],
            },
            {
                'role': 'user',
                'content': [{'type': 'input_text', 'text': prompt}],
            },
        ],
    }
    response = http_post(
        url,
        headers={
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
        },
        json=body,
        timeout=(15, 300),
    )
    response.raise_for_status()
    payload = dict(response.json() or {})
    request_id = payload.get('id') or getattr(response, 'headers', {}).get('x-amzn-requestid')
    return _extract_json(_output_text(payload)), dict(payload.get('usage') or {}), {
        'llm_model_id': MODEL_ID,
        'llm_runtime_model_id': MODEL_ID,
        'llm_provider': 'openai_on_amazon_bedrock',
        'llm_api': 'bedrock_mantle_responses',
        'llm_endpoint_family': 'bedrock-mantle',
        'llm_request_id': request_id,
        'primary_model_used': True,
    }
