from __future__ import annotations

import json
import os
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import boto3

DEFAULT_MODELS = (
    'openai.gpt-5.6-sol',
    'anthropic.claude-opus-4-8',
    'anthropic.claude-opus-4-7',
    'anthropic.claude-opus-4-6-v1',
    'anthropic.claude-sonnet-4-6-v1',
    'amazon.nova-premier-v1:0',
    'amazon.nova-pro-v1:0',
    'us.amazon.nova-2-lite-v1:0',
    'amazon.nova-lite-v1:0',
    'amazon.nova-micro-v1:0',
)
SYSTEM_PROMPT = (
    'You are the isolated MLB Auto research scientist. Return compact JSON only. '
    'Use only supplied point-in-time pregame feature names and approved numeric '
    'operations. Never use outcomes, scores, postgame data, external actions, '
    'executable code, or changes to validation, locking, isolation, or promotion rules.'
)


def configured_models() -> tuple[str, ...]:
    return tuple(
        value.strip()
        for value in os.getenv(
            'MLB_AUTO_LLM_MODEL_IDS', ','.join(DEFAULT_MODELS),
        ).split(',')
        if value.strip()
    )


def _region() -> str:
    return (
        os.getenv('AWS_REGION')
        or os.getenv('AWS_DEFAULT_REGION')
        or 'us-east-1'
    )


def _max_tokens() -> int:
    try:
        value = int(os.getenv('MLB_AUTO_LLM_MAX_OUTPUT_TOKENS', '2200'))
    except Exception:
        value = 2200
    return max(512, min(16000, value))


def _timeout() -> int:
    try:
        value = int(os.getenv('MLB_AUTO_LLM_HTTP_TIMEOUT_SECONDS', '120'))
    except Exception:
        value = 120
    return max(10, min(300, value))


def _extract_json(text: Any) -> dict:
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


def _token(provider: Callable[[], str] | None = None) -> str:
    if provider is not None:
        return str(provider())
    from aws_bedrock_token_generator import provide_token
    return str(provide_token())


def _post_json(
    *,
    url: str,
    headers: dict[str, str],
    payload: dict,
    post: Callable[..., Any] | None = None,
) -> dict:
    if post is not None:
        response = post(
            url=url,
            headers=headers,
            json=payload,
            timeout=_timeout(),
        )
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
        with urlopen(request, timeout=_timeout()) as response:
            return json.loads(response.read().decode())
    except HTTPError as exc:
        body = exc.read().decode(errors='replace')
        raise RuntimeError(f'HTTP_{exc.code}:{body[:1200]}') from exc


def _openai_text(payload: dict) -> str:
    direct = payload.get('output_text')
    if direct:
        return str(direct)
    parts: list[str] = []
    for item in payload.get('output') or []:
        if not isinstance(item, dict):
            continue
        for content in item.get('content') or []:
            if not isinstance(content, dict):
                continue
            if content.get('type') in ('output_text', 'text'):
                parts.append(str(content.get('text') or ''))
    return ''.join(parts)


def invoke_openai(
    model_id: str,
    prompt: str,
    *,
    token_provider=None,
    http_post=None,
) -> tuple[dict, dict, str]:
    payload = _post_json(
        url=f'https://bedrock-mantle.{_region()}.api.aws/openai/v1/responses',
        headers={
            'Authorization': f'Bearer {_token(token_provider)}',
            'Content-Type': 'application/json',
        },
        payload={
            'model': model_id,
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
            'max_output_tokens': _max_tokens(),
            'store': False,
        },
        post=http_post,
    )
    return (
        _extract_json(_openai_text(payload)),
        dict(payload.get('usage') or {}),
        'bedrock-mantle-openai',
    )


def invoke_anthropic(
    model_id: str,
    prompt: str,
    *,
    token_provider=None,
    http_post=None,
) -> tuple[dict, dict, str]:
    payload = _post_json(
        url=f'https://bedrock-mantle.{_region()}.api.aws/anthropic/v1/messages',
        headers={
            'x-api-key': _token(token_provider),
            'anthropic-version': '2023-06-01',
            'Content-Type': 'application/json',
        },
        payload={
            'model': model_id,
            'max_tokens': _max_tokens(),
            'system': SYSTEM_PROMPT,
            'messages': [{'role': 'user', 'content': prompt}],
        },
        post=http_post,
    )
    text = ''.join(
        str(item.get('text') or '')
        for item in payload.get('content') or []
        if isinstance(item, dict) and item.get('type') == 'text'
    )
    return (
        _extract_json(text),
        dict(payload.get('usage') or {}),
        'bedrock-mantle-anthropic',
    )


def invoke_converse(
    model_id: str,
    prompt: str,
    *,
    client=None,
) -> tuple[dict, dict, str]:
    bedrock = client or boto3.client('bedrock-runtime')
    response = bedrock.converse(
        modelId=model_id,
        system=[{'text': SYSTEM_PROMPT}],
        messages=[{'role': 'user', 'content': [{'text': prompt}]}],
        inferenceConfig={'maxTokens': min(8000, _max_tokens())},
    )
    parts = (
        ((response.get('output') or {}).get('message') or {}).get('content')
        or []
    )
    text = ''.join(
        str(item.get('text') or '')
        for item in parts
        if isinstance(item, dict)
    )
    return (
        _extract_json(text),
        dict(response.get('usage') or {}),
        'bedrock-runtime-converse',
    )


def invoke_one(
    model_id: str,
    prompt: str,
    *,
    client=None,
    token_provider=None,
    http_post=None,
) -> tuple[dict, dict, str]:
    if model_id.startswith('openai.'):
        return invoke_openai(
            model_id,
            prompt,
            token_provider=token_provider,
            http_post=http_post,
        )
    if model_id.startswith('anthropic.'):
        return invoke_anthropic(
            model_id,
            prompt,
            token_provider=token_provider,
            http_post=http_post,
        )
    return invoke_converse(model_id, prompt, client=client)


def invoke_chain(
    prompt: str,
    client=None,
    *,
    model_ids=None,
    token_provider=None,
    http_post=None,
):
    errors: list[str] = []
    for model_id in tuple(model_ids or configured_models()):
        try:
            proposal, usage, endpoint_family = invoke_one(
                model_id,
                prompt,
                client=client,
                token_provider=token_provider,
                http_post=http_post,
            )
            usage = {
                **usage,
                'endpoint_family': endpoint_family,
            }
            return proposal, usage, model_id, errors
        except Exception as exc:
            errors.append(
                f'{model_id}:{type(exc).__name__}:{str(exc)[:800]}'
            )
    raise RuntimeError('ALL_LLM_MODELS_FAILED|' + '|'.join(errors))


def install(llm_rd_module) -> None:
    models = configured_models()
    llm_rd_module.MODEL_IDS = models

    def patched_invoke(prompt, client=None):
        return invoke_chain(prompt, client, model_ids=models)

    llm_rd_module._invoke = patched_invoke


def probe_models(
    *,
    Store,
    model_ids,
    client=None,
    token_provider=None,
    http_post=None,
) -> dict:
    store = Store()
    prompt = (
        'Return exactly this JSON object and nothing else: '
        '{"health":"ok","scope":"mlb_auto"}'
    )
    results = []
    for model_id in model_ids:
        try:
            payload, usage, endpoint_family = invoke_one(
                str(model_id),
                prompt,
                client=client,
                token_provider=token_provider,
                http_post=http_post,
            )
            healthy = (
                payload.get('health') == 'ok'
                and payload.get('scope') == 'mlb_auto'
            )
            results.append({
                'model_id': str(model_id),
                'ok': healthy,
                'endpoint_family': endpoint_family,
                'usage': usage,
                'error': '' if healthy else 'INVALID_HEALTH_RESPONSE',
            })
        except Exception as exc:
            results.append({
                'model_id': str(model_id),
                'ok': False,
                'endpoint_family': (
                    'bedrock-mantle-openai'
                    if str(model_id).startswith('openai.')
                    else 'bedrock-mantle-anthropic'
                    if str(model_id).startswith('anthropic.')
                    else 'bedrock-runtime-converse'
                ),
                'error': f'{type(exc).__name__}:{str(exc)[:1600]}',
            })

    def access(name: str) -> str:
        match = next(
            (
                item for item in results
                if name in str(item.get('model_id') or '')
            ),
            None,
        )
        if match is None:
            return 'NOT_PROBED'
        return 'ENABLED' if match.get('ok') else 'BLOCKED'

    store.put_state('llm_rd', {
        'last_model_probe_results': results,
        'opus_48_access': access('claude-opus-4-8'),
        'opus_47_access': access('claude-opus-4-7'),
        'gpt_56_sol_access': access('gpt-5.6-sol'),
        'access_policy_version': 'MLB_AUTO_LLM_ACCESS_V2',
    })
    return {
        'ok': all(bool(item.get('ok')) for item in results),
        'action': 'LLM_MODEL_PROBE',
        'scope': 'mlb_auto_only',
        'results': results,
    }


def status_overlay(*, Store) -> dict:
    state = dict(Store().get_state('llm_rd') or {})
    usage = dict(state.get('llm_usage') or {})
    return {
        'mode': 'MULTI_PROVIDER_LLM_AUTONOMOUS_RD',
        'endpoint_family': usage.get('endpoint_family'),
        'opus_48_access': state.get('opus_48_access') or 'UNKNOWN',
        'opus_47_access': state.get('opus_47_access') or 'UNKNOWN',
        'gpt_56_sol_access': state.get('gpt_56_sol_access') or 'UNKNOWN',
        'last_model_probe_results': state.get('last_model_probe_results') or [],
        'access_policy_version': (
            state.get('access_policy_version')
            or 'MLB_AUTO_LLM_ACCESS_V2'
        ),
        'short_term_bearer_auth': True,
        'marketplace_auto_subscription_enabled': True,
        'scope': 'mlb_auto_only',
    }
