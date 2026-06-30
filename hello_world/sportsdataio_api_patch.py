from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, Dict

import mlb_fundamentals_engine
import sportsdataio_client


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


def _resp(status: int, body: Any) -> Dict[str, Any]:
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


def _bool(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _int(value: Any, default: int, low: int, high: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(low, min(high, parsed))


def route(event: Dict[str, Any]) -> Dict[str, Any] | None:
    event = event or {}
    method = (event.get("httpMethod") or "").upper()
    path = (event.get("path") or event.get("rawPath") or "/").rstrip("/") or "/"
    params = event.get("queryStringParameters") or {}
    if method == "OPTIONS":
        return None
    if method == "GET" and path in {"/v1/sources/mlb/sportsdataio/status", "/v1/mlb/fundamentals/status"}:
        return _resp(200, mlb_fundamentals_engine.status(fetch=_bool(params.get("fetch"))))
    if method == "GET" and path == "/v1/mlb/fundamentals/team-power":
        season = params.get("season")
        return _resp(200, mlb_fundamentals_engine.team_power_ratings(season=int(season) if season else None, limit=_int(params.get("limit"), 30, 1, 100)))
    if method == "GET" and path == "/v1/mlb/fundamentals/preview":
        season = params.get("season")
        return _resp(200, mlb_fundamentals_engine.slate_fundamentals_preview(date_yyyy_mm_dd=params.get("date"), season=int(season) if season else None))
    if method == "GET" and path == "/v1/sources/mlb/sportsdataio/raw-teams":
        if not _bool(params.get("fetch")):
            return _resp(400, {"ok": False, "error": "fetch=true required", "keyExposed": False})
        teams = sportsdataio_client.teams()
        if isinstance(teams, list):
            return _resp(200, {"ok": True, "provider": "SportsDataIO", "count": len(teams), "items": teams[:_int(params.get("limit"), 5, 1, 25)], "keyExposed": False})
        return _resp(200, {"ok": False, "provider": "SportsDataIO", "response": teams, "keyExposed": False})
    return None


def apply(api_module):
    if getattr(api_module, "_INQSI_SPORTSDATAIO_ROUTES_APPLIED", False):
        return api_module
    original = api_module.lambda_handler

    def patched_lambda_handler(event, context):
        routed = route(event or {})
        if routed is not None:
            return routed
        return original(event, context)

    api_module.lambda_handler = patched_lambda_handler
    api_module._INQSI_SPORTSDATAIO_ROUTES_APPLIED = True
    return api_module
