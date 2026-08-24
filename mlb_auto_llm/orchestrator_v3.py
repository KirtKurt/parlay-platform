from __future__ import annotations

from typing import Any

try:
    import model_gateway as legacy_gateway
    import production_model_gateway as production_gateway
except ModuleNotFoundError:  # pragma: no cover - package import in unit tests
    from mlb_auto_llm import model_gateway as legacy_gateway
    from mlb_auto_llm import production_model_gateway as production_gateway

# orchestrator_v2 imports these symbols directly. Patch the module before that
# import so both the deployment smoke and authoritative card use the same
# endpoint-native catalog, bounded attempt policy, and cooldown state.
legacy_gateway.configured_models = production_gateway.configured_models
legacy_gateway.invoke_chain_text = production_gateway.invoke_chain_text
legacy_gateway.invoke_text = production_gateway.invoke_text
legacy_gateway.reset_model_state = production_gateway.reset_model_state

try:
    import orchestrator_v2 as production
except ModuleNotFoundError:  # pragma: no cover - package import in unit tests
    from mlb_auto_llm import orchestrator_v2 as production


def lambda_handler(event: Any, context: Any) -> Any:
    return production.lambda_handler(event, context)
