from __future__ import annotations

from typing import Any

from . import llm_rd as _llm_rd
from . import model_access as _model_access
from .mantle_provider import invoke as _invoke_model


def _invoke_rd(prompt, client=None):
    errors = []
    for model_id in _llm_rd.MODEL_IDS:
        try:
            proposal, usage = _invoke_model(
                model_id,
                prompt,
                max_tokens=_llm_rd.MAX_OUTPUT_TOKENS,
                runtime_client=client,
            )
            return proposal, usage, model_id, errors
        except Exception as exc:
            errors.append(
                f'{model_id}:{type(exc).__name__}:{str(exc)[:900]}'
            )
    raise RuntimeError('ALL_BEDROCK_ENDPOINTS_FAILED|' + '|'.join(errors))


def _invoke_opus_probe(runtime, runtime_model_id: str) -> dict[str, Any]:
    try:
        payload, usage = _invoke_model(
            runtime_model_id,
            'Return exactly this JSON object and nothing else: '
            '{"mlb_auto_opus_access":true}',
            max_tokens=512,
            runtime_client=runtime,
        )
        return {
            'ok': payload.get('mlb_auto_opus_access') is True,
            'response_confirmed': payload.get('mlb_auto_opus_access') is True,
            'usage': usage,
            'endpoint_family': usage.get('endpoint_family'),
            'foundation_model_id': usage.get('foundation_model_id'),
        }
    except Exception as exc:
        return {
            'ok': False,
            'error_code': type(exc).__name__,
            'error': str(exc)[:1800],
        }


_llm_rd._invoke = _invoke_rd
_model_access._invoke_probe = _invoke_opus_probe

from .autonomous_handler import handler  # noqa: E402,F401
