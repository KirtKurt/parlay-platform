from __future__ import annotations

from typing import Any, Dict

try:
    import bedrock_smoke
    import ml_authority
except ModuleNotFoundError:  # pragma: no cover - package import in unit tests
    from mlb_auto_llm import bedrock_smoke, ml_authority


def lambda_handler(event: Any, context: Any) -> Dict[str, Any]:
    """Verify Bedrock first, then the trained AWS ML authority if Bedrock is unavailable.

    This is deployment verification only. It never supplies a human/market winner,
    never bypasses the production three-source coverage contract, and never weakens
    immutable publication or timing controls.
    """

    try:
        value = bedrock_smoke.lambda_handler(event, context)
        bedrock_result = (
            value
            if isinstance(value, dict)
            else {
                "ok": False,
                "error": "BEDROCK_SMOKE_RESPONSE_NOT_OBJECT",
            }
        )
    except Exception as exc:  # pragma: no cover - live provider failure path
        bedrock_result = {
            "ok": False,
            "errorType": type(exc).__name__,
            "error": str(exc)[:2000],
        }

    if (
        bedrock_result.get("ok") is True
        and bedrock_result.get("responseNonEmpty") is True
    ):
        bedrock_result["decisionAuthority"] = "BEDROCK_LLM"
        bedrock_result["bedrockAvailable"] = True
        bedrock_result["mlFallbackAttempted"] = False
        return bedrock_result

    ml_result = dict(ml_authority.smoke())
    ml_result["bedrockAvailable"] = False
    ml_result["mlFallbackAttempted"] = True
    ml_result["bedrockAttemptedModelIds"] = (
        bedrock_result.get("attemptedModelIds") or []
    )
    ml_result["bedrockErrors"] = (
        bedrock_result.get("errors")
        or bedrock_result.get("errorsBeforeSuccess")
        or [
            {
                "errorType": bedrock_result.get("errorType"),
                "error": bedrock_result.get("error"),
            }
        ]
    )
    return ml_result
