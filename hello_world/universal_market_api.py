from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, Dict

from universal_market_language import market_language_status


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


def _resp(status: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json",
            "access-control-allow-origin": "*",
            "access-control-allow-headers": "content-type",
            "access-control-allow-methods": "GET,POST,OPTIONS",
        },
        "body": json.dumps(body, default=_json_default),
    }


def lambda_handler(event, context):
    event = event or {}
    method = (event.get("httpMethod") or "").upper()
    path = event.get("path") or ""
    if method == "OPTIONS":
        return _resp(200, {"ok": True})
    if method == "GET" and path in {"/v1/market-language/status", "/v1/sources/market-language/status"}:
        return _resp(200, market_language_status())
    return _resp(404, {"ok": False, "error": f"Route not found: {method} {path}"})
