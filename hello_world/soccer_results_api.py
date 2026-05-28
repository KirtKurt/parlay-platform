from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, Dict

from soccer_audit import soccer_results_audit, soccer_results_status


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
    params = event.get("queryStringParameters") or {}
    if method == "OPTIONS":
        return _resp(200, {"ok": True})
    try:
        if method == "GET" and path in {"/v1/results/soccer/status", "/v1/results/soccer/audit"}:
            fetch_live = str(params.get("fetch_live", "false")).lower() == "true"
            if path == "/v1/results/soccer/audit" or fetch_live:
                return _resp(200, soccer_results_audit(
                    slate_date=params.get("slate_date_et"),
                    fetch_live=fetch_live,
                    days_from=int(params.get("days_from") or 3),
                ))
            return _resp(200, soccer_results_status(params.get("slate_date_et")))
        if method == "POST" and path == "/v1/results/soccer/audit":
            body = json.loads(event.get("body") or "{}")
            return _resp(200, soccer_results_audit(
                slate_date=body.get("slate_date_et"),
                manual_results=body.get("results") or [],
                fetch_live=bool(body.get("fetch_live", False)),
                days_from=int(body.get("days_from") or 3),
            ))
        return _resp(404, {"ok": False, "error": f"Route not found: {method} {path}"})
    except Exception as exc:
        return _resp(500, {"ok": False, "sport": "soccer", "error": str(exc)})
