from __future__ import annotations

from typing import Any, Dict

try:
    import model_gateway as legacy_gateway
    import production_model_gateway as production_gateway
except ModuleNotFoundError:  # pragma: no cover - package import in unit tests
    from mlb_auto_llm import model_gateway as legacy_gateway
    from mlb_auto_llm import production_model_gateway as production_gateway


def _invoke_chain_for_v2(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    """Expose the full endpoint route to v2 retry removal.

    ``orchestrator_v2`` removes the returned ``modelId`` after a schema-invalid
    response. Mantle and cross-region routes contain endpoint/region identity;
    returning only the provider model name would leave the failed route in the
    remaining list and could retry it indefinitely.
    """

    result = dict(production_gateway.invoke_chain_text(*args, **kwargs))
    if result.get("ok") is True and result.get("routeId"):
        result["resolvedModelId"] = result.get("modelId")
        result["modelId"] = result.get("routeId")
    return result


# Patch before importing v2 so its direct symbol imports bind to the production
# gateway used by the deployment smoke.
legacy_gateway.configured_models = production_gateway.configured_models
legacy_gateway.invoke_chain_text = _invoke_chain_for_v2
legacy_gateway.invoke_text = production_gateway.invoke_text
legacy_gateway.reset_model_state = production_gateway.reset_model_state

try:
    import orchestrator_v2 as production
except ModuleNotFoundError:  # pragma: no cover - package import in unit tests
    from mlb_auto_llm import orchestrator_v2 as production


def lambda_handler(event: Any, context: Any) -> Any:
    return production.lambda_handler(event, context)
