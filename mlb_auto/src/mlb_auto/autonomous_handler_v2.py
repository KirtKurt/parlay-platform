from __future__ import annotations

from . import autonomous_handler as runtime
from . import llm_rd
from .llm_access_v2 import install, probe_models, status_overlay
from .storage import Store

install(llm_rd)


def handler(event, context):
    event = event or {}
    action = str(event.get('action') or event.get('detail-type') or '').upper()
    if action in ('LLM_MODEL_PROBE', 'MLB_AUTO_LLM_MODEL_PROBE'):
        model_ids = [
            str(value) for value in (event.get('model_ids') or [])
            if str(value).strip()
        ]
        if not model_ids:
            model_ids = [
                'openai.gpt-5.6-sol',
                'anthropic.claude-opus-4-8',
                'anthropic.claude-opus-4-7',
            ]
        return probe_models(Store=Store, model_ids=model_ids)

    result = runtime.handler(event, context)
    if action in ('STATUS', 'MLB_AUTO_STATUS') and isinstance(result, dict):
        layer = dict(result.get('llm_rd') or {})
        layer.update(status_overlay(Store=Store))
        result['llm_rd'] = layer
    return result
