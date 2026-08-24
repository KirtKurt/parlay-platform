from __future__ import annotations

from typing import Any, Dict

try:
    from bedrock_smoke import lambda_handler as _bedrock_smoke
except ModuleNotFoundError:  # pragma: no cover - package import in unit tests
    from mlb_auto_llm.bedrock_smoke import lambda_handler as _bedrock_smoke


def lambda_handler(event: Any, context: Any) -> Dict[str, Any]:
    """Deployment proof is Bedrock-only and never hides model errors behind ML."""

    result = dict(_bedrock_smoke(event, context))
    result["decisionAuthority"] = "BEDROCK_LLM"
    result["bedrockAvailable"] = result.get("ok") is True
    result["mlFallbackAttempted"] = False
    return result
