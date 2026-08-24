from __future__ import annotations

from typing import Any, Dict

from model_gateway import configured_models, discovered_models, invoke_chain_text


def lambda_handler(event: Any, context: Any) -> Dict[str, Any]:
    discovered = discovered_models()
    models = configured_models()
    result = invoke_chain_text(
        "Return only the word OK.",
        models,
        max_tokens=8,
        temperature=0.0,
        top_p=0.9,
    )
    if result.get("ok") is not True:
        return {
            "ok": False,
            "service": "mlb-auto-llm-bedrock-smoke",
            "attemptedModelIds": result.get("attemptedModelIds") or [],
            "configuredModelCount": len(models),
            "discoveredModelCount": len(discovered),
            "discoveredModelIds": discovered,
            "errors": result.get("errors") or [],
        }
    text = str(result.get("text") or "").strip()
    if not text:
        return {
            "ok": False,
            "service": "mlb-auto-llm-bedrock-smoke",
            "attemptedModelIds": result.get("attemptedModelIds") or [],
            "configuredModelCount": len(models),
            "discoveredModelCount": len(discovered),
            "discoveredModelIds": discovered,
            "errors": [{"errorCode": "EMPTY_BEDROCK_RESPONSE"}],
        }
    return {
        "ok": True,
        "service": "mlb-auto-llm-bedrock-smoke",
        "modelId": result.get("modelId"),
        "endpointFamily": result.get("endpointFamily"),
        "responseNonEmpty": True,
        "configuredModelCount": len(models),
        "discoveredModelCount": len(discovered),
        "discoveredModelIds": discovered,
        "attemptedModelIds": result.get("attemptedModelIds") or [],
        "errorsBeforeSuccess": result.get("errorsBeforeSuccess") or [],
    }
