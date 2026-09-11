"""Lambda + in-process routes for the arb desk.

Routes
  GET  /v1/arb/health
  GET  /v1/arb/scan?sport=mlb&bankroll=1000&markets=h2h,spreads,totals
  POST /v1/scan            raw {bankroll, events:[{id,event,market,quotes}]}
  POST /v1/arb/scan        same as /v1/scan
"""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, Dict, Optional

try:
    from arb_engine import scan_all
    from arb_feeds import live_scan_payload
except ImportError:  # pragma: no cover
    from hello_world.arb_engine import scan_all
    from hello_world.arb_feeds import live_scan_payload


VERSION = "ARB-DESK-v1"


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


def _parse_json(body: Optional[str]) -> Dict[str, Any]:
    if not body:
        return {}
    try:
        payload = json.loads(body)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _qs(event: Dict[str, Any]) -> Dict[str, str]:
    raw = event.get("queryStringParameters") or {}
    return {str(k): str(v) for k, v in raw.items() if v is not None}


def handle_arb_request(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return a response if this event is an arb route, else None."""
    event = event or {}
    method = (event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method") or "").upper()
    path = event.get("path") or event.get("rawPath") or "/"
    if method == "OPTIONS" and path.startswith("/v1/") and ("arb" in path or path == "/v1/scan"):
        return _resp(200, {"ok": True})
    if method == "GET" and path == "/v1/arb/health":
        return _resp(200, {"ok": True, "service": "arb-desk", "version": VERSION, "places_bets": False})
    if method == "GET" and path in {"/v1/arb/scan", "/v1/arbs"}:
        params = _qs(event)
        sport = (params.get("sport") or "mlb").lower()
        try:
            bankroll = float(params.get("bankroll") or 1000)
        except (TypeError, ValueError):
            bankroll = 1000.0
        markets = params.get("markets") or "h2h,spreads,totals"
        payload = live_scan_payload(sport=sport, bankroll=bankroll, markets=markets)
        result = scan_all(payload)
        result["status"] = payload.get("status")
        result["version"] = VERSION
        return _resp(200, result)
    if method == "POST" and path in {"/v1/scan", "/v1/arb/scan"}:
        body = _parse_json(event.get("body"))
        if "events" not in body:
            return _resp(400, {"ok": False, "error": "BODY_EVENTS_REQUIRED"})
        result = scan_all(body)
        result["version"] = VERSION
        result["status"] = {"source": "posted", "places_bets": False}
        return _resp(200, result)
    return None


def lambda_handler(event, context):
    handled = handle_arb_request(event or {})
    if handled is not None:
        return handled
    return _resp(404, {"ok": False, "error": "ARB_ROUTE_NOT_FOUND"})
