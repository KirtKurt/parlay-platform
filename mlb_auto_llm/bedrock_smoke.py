from __future__ import annotations

import os
from typing import Any, Dict, Iterable, List, Sequence, Tuple

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


def _runtime_route_priority(route_id: str) -> Tuple[int, str]:
    """Prefer AWS-managed cross-Region profiles over exhausted direct pools."""

    value = str(route_id or "").strip()
    model_id = value.split("::", 1)[-1].lower()
    if model_id.startswith("us."):
        return (0, value)
    if model_id.startswith("global."):
        return (1, value)
    return (2, value)


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


def _dedupe(values: Iterable[str]) -> List[str]:
    output: List[str] = []
    seen = set()
    for raw in values:
        value = str(raw or "").strip()
        if value and value not in seen:
            seen.add(value)
            output.append(value)
    return output


def _round_robin(groups: Sequence[Sequence[str]]) -> List[str]:
    output: List[str] = []
    seen = set()
    width = max((len(group) for group in groups), default=0)
    for index in range(width):
        for group in groups:
            if index >= len(group):
                continue
            value = str(group[index] or "").strip()
            if value and value not in seen:
                seen.add(value)
                output.append(value)
    return output


def _diversified_routes(
    *,
    mantle: Sequence[str],
    runtime: Sequence[str],
    catalog: Sequence[str],
) -> List[str]:
    """Mix independent endpoint pools while retaining cross-Region priority."""

    runtime_ordered = sorted(_dedupe(runtime), key=_runtime_route_priority)
    mantle_ordered = _dedupe(mantle)
    occupied = set(runtime_ordered) | set(mantle_ordered)
    remaining = [value for value in _dedupe(catalog) if value not in occupied]
    return _round_robin((runtime_ordered, mantle_ordered, remaining))


def lambda_handler(event: Any, context: Any) -> Dict[str, Any]:
    # Refresh endpoint-native catalogs, but retain warm-container failure
    # cooldowns so a deployment probe cannot repeatedly hammer routes already
    # proven unavailable, EOL, account-denied, or daily-token exhausted.
    reset_model_state(clear_discovery=True, clear_failures=False)
    mantle = mantle_models()
    runtime = runtime_models()
    catalog = configured_models()

    # A health check must prove the Bedrock service, not a single model/Region.
    # Interleave AWS-managed cross-Region Runtime profiles with the endpoint-
    # native Mantle pool, then use the remaining configured catalog. This keeps
    # the probe bounded while surviving one depleted per-model or per-Region
    # daily-token allocation.
    route_limit = _route_limit()
    diversified = _diversified_routes(
        mantle=mantle,
        runtime=runtime,
        catalog=catalog,
    )
    models = diversified[:route_limit]
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
        "configuredRouteCatalogCount": len(catalog),
        "smokeRouteLimit": route_limit,
        "routeAttemptLimit": attempt_limit,
        "routeSelectionPolicy": (
            "round-robin-cross-region-runtime,mantle,remaining-catalog"
        ),
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
