from __future__ import annotations

import os
from typing import Any, Dict

from model_gateway import invoke_chain_text


MODELS = [
    item.strip()
    for item in os.environ.get(
        "MLB_AUTO_BEDROCK_MODELS",
        "openai.gpt-5.6-sol,anthropic.claude-opus-4-8,anthropic.claude-opus-4-7,anthropic.claude-opus-4-6-v1,anthropic.claude-sonnet-4-6-v1,us.amazon.nova-2-lite-v1:0,global.amazon.nova-2-lite-v1:0,us.amazon.nova-pro-v1:0,us.amazon.nova-lite-v1:0,us.amazon.nova-micro-v1:0",
    ).split(",")
    if item.strip()
]


def lambda_handler(event: Any, context: Any) -> Dict[str, Any]:
    result = invoke_chain_text(
        "Return only the word OK.",
        MODELS,
        max_tokens=16,
        temperature=0.0,
        top_p=0.9,
    )
    if result.get("ok") is not True:
        return {
            "ok": False,
            "service": "mlb-auto-llm-bedrock-smoke",
            "attemptedModelIds": result.get("attemptedModelIds") or MODELS,
            "errors": result.get("errors") or [],
        }
    text = str(result.get("text") or "").strip()
    if not text:
        return {
            "ok": False,
            "service": "mlb-auto-llm-bedrock-smoke",
            "attemptedModelIds": result.get("attemptedModelIds") or [],
            "errors": [{"errorCode": "EMPTY_BEDROCK_RESPONSE"}],
        }
    return {
        "ok": True,
        "service": "mlb-auto-llm-bedrock-smoke",
        "modelId": result.get("modelId"),
        "endpointFamily": result.get("endpointFamily"),
        "responseNonEmpty": True,
        "attemptedModelIds": result.get("attemptedModelIds") or [],
        "errorsBeforeSuccess": result.get("errorsBeforeSuccess") or [],
    }
