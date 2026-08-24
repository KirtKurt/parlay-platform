from __future__ import annotations

from typing import Any, Dict

import bedrock_smoke
import ml_authority


def lambda_handler(event: Any, context: Any) -> Dict[str, Any]:
    bedrock_result: Dict[str, Any]
    try:
        value = bedrock_smoke.lambda_handler(event, context)
        bedrock_result = value if isinstance(value, dict) else {
            "ok": False,
            "error": "BEDROCK_SMOKE_RESPONSE_NOT_OBJECT",
        }
    except Exception as exc:
        bedrock_result = {
            "ok": False,
            "errorType": type(exc).__name__,
            "error": str(exc)[:2000],
        }

    if bedrock_result.get("ok") is True and bedrock_result.get("responseNonEmpty") is True:
        bedrock_result["decisionAuthority"] = "BEDROCK_LLM"
        bedrock_result["bedrockAvailable"] = True
        return bedrock_result

    ml_result = ml_authority.smoke()
    ml_result["bedrockAvailable"] = False
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
