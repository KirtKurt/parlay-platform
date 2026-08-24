from __future__ import annotations

from typing import Any, Dict

try:
    from production_model_gateway import (
        configured_models,
        invoke_chain_text,
        mantle_models,
        reset_model_state,
        runtime_models,
    )
    from model_gateway import configured_regions
except ModuleNotFoundError:  # pragma: no cover - package import in unit tests
    from mlb_auto_llm.production_model_gateway import (
        configured_models,
        invoke_chain_text,
        mantle_models,
        reset_model_state,
        runtime_models,
    )
    from mlb_auto_llm.model_gateway import configured_regions


def lambda_handler(event: Any, context: Any) -> Dict[str, Any]:
    # Refresh endpoint-native catalogs, but retain warm-container cooldowns so
    # a deployment probe cannot repeatedly hammer permanently denied routes.
    reset_model_state(clear_discovery=True, clear_failures=False)
    mantle = mantle_models()
    runtime = runtime_models()
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
        "mantleModelCount": len(mantle),
        "runtimeModelCount": len(runtime),
        "mantleModelIds": mantle,
        "runtimeModelIds": runtime,
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
