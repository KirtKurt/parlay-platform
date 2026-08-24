from __future__ import annotations

from typing import Any, Dict

from model_gateway import (
    configured_models,
    configured_regions,
    discovered_models,
    invoke_chain_text,
)


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
    common = {
        "service": "mlb-auto-llm-bedrock-smoke",
        "configuredRegions": configured_regions(),
        "configuredModelCount": len(models),
        "discoveredModelCount": len(discovered),
        "discoveredModelIds": discovered,
        "attemptedModelIds": result.get("attemptedModelIds") or [],
    }
    if result.get("ok") is not True:
        return {
            **common,
            "ok": False,
            "errors": result.get("errors") or [],
        }
    text = str(result.get("text") or "").strip()
    if not text:
        return {
            **common,
            "ok": False,
            "errors": [{"errorCode": "EMPTY_BEDROCK_RESPONSE"}],
        }
    return {
        **common,
        "ok": True,
        "routeId": result.get("routeId"),
        "region": result.get("region"),
        "modelId": result.get("modelId"),
        "endpointFamily": result.get("endpointFamily"),
        "responseNonEmpty": True,
        "errorsBeforeSuccess": result.get("errorsBeforeSuccess") or [],
    }
