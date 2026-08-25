from __future__ import annotations

import os
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
    # Refresh endpoint-native catalogs, but retain warm-container failure
    # cooldowns so a deployment probe cannot repeatedly hammer routes already
    # proven unavailable, EOL, account-denied, or daily-token exhausted.
    reset_model_state(clear_discovery=True, clear_failures=False)
    mantle = mantle_models()
    runtime = runtime_models()

    # A health check must prove the Bedrock service, not a single model/Region.
    # Keep the probe bounded, but allow enough model- and Region-diverse routes
    # to survive an exhausted per-model daily-token pool.
    route_limit = max(
        1, int(os.getenv("MLB_AUTO_BEDROCK_SMOKE_ROUTE_LIMIT", "12"))
    )
    models = list(runtime[:route_limit])
    attempt_limit = max(1, len(models))
    result = invoke_chain_text(
        "Return only the word OK.",
        models,
        max_tokens=8,
        temperature=0.0,
        top_p=0.9,
        max_attempts=attempt_limit,
    )
    common = {
        "service": "mlb-auto-llm-bedrock-smoke",
        "configuredRegions": configured_regions(),
        "configuredModelCount": len(models),
        "routeAttemptLimit": attempt_limit,
        "failoverEnabled": len(models) > 1,
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
