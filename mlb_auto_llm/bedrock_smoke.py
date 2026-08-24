from __future__ import annotations

import json
import os
from typing import Any, Dict

import boto3

MODELS = [
    item.strip()
    for item in os.environ.get(
        "MLB_AUTO_BEDROCK_MODELS",
        "us.amazon.nova-2-lite-v1:0,global.amazon.nova-2-lite-v1:0,us.amazon.nova-pro-v1:0,us.amazon.nova-lite-v1:0,us.amazon.nova-micro-v1:0",
    ).split(",")
    if item.strip()
]


def lambda_handler(event: Any, context: Any) -> Dict[str, Any]:
    client = boto3.client("bedrock-runtime")
    errors = []
    for model_id in MODELS:
        try:
            response = client.converse(
                modelId=model_id,
                messages=[{"role": "user", "content": [{"text": "Return only the word OK."}]}],
                inferenceConfig={"maxTokens": 16, "temperature": 0},
            )
            text = " ".join(
                str(block.get("text") or "")
                for block in (((response.get("output") or {}).get("message") or {}).get("content") or [])
                if isinstance(block, dict)
            ).strip()
            if not text:
                raise RuntimeError("EMPTY_BEDROCK_RESPONSE")
            return {
                "ok": True,
                "service": "mlb-auto-llm-bedrock-smoke",
                "modelId": model_id,
                "responseNonEmpty": True,
                "attemptedModelIds": [row["modelId"] for row in errors] + [model_id],
                "errorsBeforeSuccess": errors,
            }
        except Exception as exc:
            error_code = str((getattr(exc, "response", {}) or {}).get("Error", {}).get("Code", ""))
            errors.append({
                "modelId": model_id,
                "errorCode": error_code or type(exc).__name__,
                "message": str(exc)[:240],
            })
    return {
        "ok": False,
        "service": "mlb-auto-llm-bedrock-smoke",
        "attemptedModelIds": MODELS,
        "errors": errors,
    }
