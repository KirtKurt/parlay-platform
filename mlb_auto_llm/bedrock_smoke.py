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


DEFAULT_SMOKE_ROUTE_LIMIT = 16
MAX_SMOKE_ROUTE_LIMIT = 32


def _route_limit() -> int:
    try:
        configured = int(
            os.getenv(
                "MLB_AUTO_BEDROCK_SMOKE_ROUTE_LIMIT",
                str(DEFAULT_SMOKE_ROUTE_LIMIT),
            )
        )
    except (TypeError, ValueError):
        configured = DEFAULT_SMOKE_ROUTE_LIMIT
    return min(MAX_SMOKE_ROUTE_LIMIT, max(1, configured))


def lambda_handler(event: Any, context: Any) -> Dict[str, Any]:
    # Refresh endpoint-native catalogs, but retain warm-container failure
    # cooldowns so a deployment probe does not repeatedly hammer routes already
    # proven unavailable, EOL, account-denied, or daily-token exhausted.
    reset_model_state(clear_discovery=True, clear_failures=False)
    mantle = mantle_models()
    runtime = runtime_models()
    catalog = configured_models()
    route_limit = _route_limit()
    models = list(catalog[:route_limit])
    result = invoke_chain_text(
        "Return only the word OK.",
        models,
        max_tokens=8,
        temperature=0.0,
        top_p=0.9,
        max_attempts=route_limit,
    )
    common = {
        "service": "mlb-auto-llm-bedrock-smoke",
        "configuredRegions": configured_regions(),
        "configuredModelCount": len(models),
        "configuredRouteCatalogCount": len(catalog),
        "smokeRouteLimit": route_limit,
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
