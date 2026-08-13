from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Callable

import boto3

OPUS_MODELS = (
    {
        'name': 'Claude Opus 4.8',
        'foundation_model_id': 'anthropic.claude-opus-4-8',
        'runtime_model_id': 'us.anthropic.claude-opus-4-8',
        'marketplace_product_id': 'prod-bk5rjg4eo2pke',
    },
    {
        'name': 'Claude Opus 4.7',
        'foundation_model_id': 'anthropic.claude-opus-4-7',
        'runtime_model_id': 'us.anthropic.claude-opus-4-7',
        'marketplace_product_id': 'prod-d2ik6zgct5hxi',
    },
)


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _error_code(exc: Exception) -> str:
    response = getattr(exc, 'response', None) or {}
    return str((response.get('Error') or {}).get('Code') or type(exc).__name__)


def _error_message(exc: Exception) -> str:
    response = getattr(exc, 'response', None) or {}
    message = (response.get('Error') or {}).get('Message')
    return str(message or exc)[:800]


def _availability_ready(payload: dict[str, Any]) -> bool:
    agreement = str(((payload.get('agreementAvailability') or {}).get('status')) or '')
    authorization = str(payload.get('authorizationStatus') or '')
    entitlement = str(payload.get('entitlementAvailability') or '')
    region = str(payload.get('regionAvailability') or '')
    return (
        agreement == 'AVAILABLE'
        and authorization == 'AUTHORIZED'
        and entitlement == 'AVAILABLE'
        and region == 'AVAILABLE'
    )


def _safe_availability(client, model_id: str) -> dict[str, Any]:
    try:
        response = client.get_foundation_model_availability(modelId=model_id)
        return {'ok': True, **dict(response or {})}
    except Exception as exc:
        return {
            'ok': False,
            'modelId': model_id,
            'error_code': _error_code(exc),
            'error': _error_message(exc),
        }


def _ensure_anthropic_use_case(client) -> dict[str, Any]:
    try:
        response = client.get_use_case_for_model_access()
        return {'ok': True, 'submitted': False, 'already_present': True, 'response': response}
    except Exception as exc:
        if _error_code(exc) != 'ResourceNotFoundException':
            return {
                'ok': False,
                'submitted': False,
                'error_code': _error_code(exc),
                'error': _error_message(exc),
            }

    form_data = {
        'companyName': os.getenv('MLB_AUTO_ANTHROPIC_COMPANY_NAME', 'Inqis'),
        'companyWebsite': os.getenv(
            'MLB_AUTO_ANTHROPIC_COMPANY_WEBSITE',
            'https://github.com/KirtKurt/parlay-platform',
        ),
        'intendedUsers': os.getenv('MLB_AUTO_ANTHROPIC_INTENDED_USERS', '0'),
        'industryOption': os.getenv('MLB_AUTO_ANTHROPIC_INDUSTRY', 'Technology'),
        'otherIndustryOption': os.getenv(
            'MLB_AUTO_ANTHROPIC_OTHER_INDUSTRY',
            'Sports analytics and machine learning',
        ),
        'useCases': os.getenv(
            'MLB_AUTO_ANTHROPIC_USE_CASES',
            'Internal MLB Auto research and development. The model proposes pregame '
            'numeric feature interactions from point-in-time sports market data. '
            'All proposals remain isolated to MLB Auto and require chronological '
            'validation before any production activation.',
        ),
    }
    try:
        client.put_use_case_for_model_access(formData=json.dumps(form_data))
        return {'ok': True, 'submitted': True, 'already_present': False}
    except Exception as exc:
        return {
            'ok': False,
            'submitted': False,
            'error_code': _error_code(exc),
            'error': _error_message(exc),
        }


def _create_agreement(client, model_id: str) -> dict[str, Any]:
    try:
        try:
            offers = client.list_foundation_model_agreement_offers(
                modelId=model_id,
                offerType='ALL',
            )
        except Exception as exc:
            # Older SDK service models may not expose offerType even though the API does.
            if _error_code(exc) not in ('ParamValidationError', 'UnknownParameterError'):
                raise
            offers = client.list_foundation_model_agreement_offers(modelId=model_id)
        rows = list((offers or {}).get('offers') or [])
        offer = next((row for row in rows if row.get('offerToken')), None)
        if not offer:
            return {
                'ok': False,
                'created': False,
                'reason': 'NO_FOUNDATION_MODEL_AGREEMENT_OFFER',
            }
        client.create_foundation_model_agreement(
            modelId=model_id,
            offerToken=str(offer['offerToken']),
        )
        return {
            'ok': True,
            'created': True,
            'offer_id': offer.get('offerId'),
        }
    except Exception as exc:
        code = _error_code(exc)
        message = _error_message(exc)
        if code in ('ConflictException', 'ResourceInUseException') or 'already' in message.lower():
            return {'ok': True, 'created': False, 'already_present': True}
        return {
            'ok': False,
            'created': False,
            'error_code': code,
            'error': message,
        }


def _invoke_probe(runtime, runtime_model_id: str) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        'modelId': runtime_model_id,
        'messages': [{
            'role': 'user',
            'content': [{
                'text': 'Return exactly this JSON object and nothing else: {"mlb_auto_opus_access":true}',
            }],
        }],
        'inferenceConfig': {'maxTokens': 512},
    }
    if 'claude-opus-4-7' in runtime_model_id:
        kwargs['additionalModelRequestFields'] = {
            'thinking': {'type': 'adaptive'},
            'output_config': {'effort': 'low'},
        }
    try:
        response = runtime.converse(**kwargs)
        metadata = dict(response.get('ResponseMetadata') or {})
        return {
            'ok': True,
            'request_id': metadata.get('RequestId'),
            'usage': dict(response.get('usage') or {}),
        }
    except Exception as exc:
        return {
            'ok': False,
            'error_code': _error_code(exc),
            'error': _error_message(exc),
        }


def ensure_opus_access(
    *,
    Store,
    bedrock_client=None,
    runtime_client=None,
    sleep: Callable[[float], None] = time.sleep,
    poll_attempts: int = 36,
    poll_seconds: int = 10,
) -> dict[str, Any]:
    """Enable and prove Opus 4.8/4.7 access for the isolated MLB Auto runtime."""
    store = Store()
    region = os.getenv('AWS_REGION') or os.getenv('AWS_DEFAULT_REGION') or 'us-east-1'
    bedrock = bedrock_client or boto3.client('bedrock', region_name=region)
    runtime = runtime_client or boto3.client('bedrock-runtime', region_name=region)
    started_at = _iso()

    use_case = _ensure_anthropic_use_case(bedrock)
    model_rows: list[dict[str, Any]] = []
    for spec in OPUS_MODELS:
        before = _safe_availability(bedrock, spec['foundation_model_id'])
        agreement = {'ok': True, 'created': False, 'not_needed': True}
        if not _availability_ready(before):
            agreement = _create_agreement(bedrock, spec['foundation_model_id'])
        model_rows.append({
            **spec,
            'availability_before': before,
            'agreement': agreement,
            'availability_after': before,
        })

    for _ in range(max(1, int(poll_attempts))):
        all_ready = True
        for row in model_rows:
            current = _safe_availability(bedrock, row['foundation_model_id'])
            row['availability_after'] = current
            row['access_ready'] = _availability_ready(current)
            all_ready = all_ready and bool(row['access_ready'])
        if all_ready:
            break
        sleep(max(0, int(poll_seconds)))

    for row in model_rows:
        row['invocation_probe'] = (
            _invoke_probe(runtime, row['runtime_model_id'])
            if row.get('access_ready')
            else {'ok': False, 'reason': 'MODEL_ACCESS_NOT_READY'}
        )

    ok = bool(use_case.get('ok')) and all(
        row.get('access_ready') and (row.get('invocation_probe') or {}).get('ok')
        for row in model_rows
    )
    completed_at = _iso()
    payload = {
        'ok': ok,
        'action': 'ENABLE_OPUS_ACCESS',
        'scope': 'mlb_auto_only',
        'region': region,
        'started_at': started_at,
        'completed_at': completed_at,
        'use_case': use_case,
        'models': model_rows,
        'all_models_ready': all(bool(row.get('access_ready')) for row in model_rows),
        'all_invocations_ok': all(
            bool((row.get('invocation_probe') or {}).get('ok')) for row in model_rows
        ),
    }
    store.put_state('llm_model_access', {
        'last_attempt_at': completed_at,
        'last_attempt_ok': ok,
        'scope': 'mlb_auto_only',
        'region': region,
        'use_case_ok': bool(use_case.get('ok')),
        'models': model_rows,
        'all_models_ready': payload['all_models_ready'],
        'all_invocations_ok': payload['all_invocations_ok'],
    })
    return payload
