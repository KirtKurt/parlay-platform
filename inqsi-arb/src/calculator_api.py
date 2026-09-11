from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, Dict

from exchange_engine import ExchangeArbError, back_lay_plan
from stake_rounding import StakeRoundingError, optimize_rounding_neighborhood

VERSION = "INQSI-ARB-v3"


def _response(status: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json",
            "access-control-allow-origin": "*",
            "access-control-allow-headers": "content-type,x-inqsi-user-id",
            "access-control-allow-methods": "POST,OPTIONS",
            "cache-control": "no-store",
        },
        "body": json.dumps(body, default=lambda v: float(v) if isinstance(v, Decimal) else str(v)),
    }


def _method(event: Dict[str, Any]) -> str:
    return str(event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method") or "").upper()


def _path(event: Dict[str, Any]) -> str:
    return str(event.get("rawPath") or event.get("path") or "/")


def _body(event: Dict[str, Any]) -> Dict[str, Any]:
    try:
        value = json.loads(event.get("body") or "{}")
        return value if isinstance(value, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def lambda_handler(event, context):
    event = event or {}
    method, path = _method(event), _path(event)
    if method == "OPTIONS":
        return _response(200, {"ok": True, "version": VERSION})
    if method != "POST":
        return _response(405, {"ok": False, "error": "METHOD_NOT_ALLOWED", "version": VERSION})

    payload = _body(event)
    try:
        if path == "/v1/arb/exchange/back-lay":
            result = back_lay_plan(
                back_odds=payload.get("back_odds"),
                back_stake=payload.get("back_stake"),
                lay_odds=payload.get("lay_odds"),
                commission_rate=payload.get("commission_rate", 0),
                lay_liquidity=payload.get("lay_liquidity"),
                max_liability=payload.get("max_liability"),
                fixed_costs=payload.get("fixed_costs", 0),
            )
            return _response(200, {"version": VERSION, **result})

        if path == "/v1/arb/stakes/verify":
            result = optimize_rounding_neighborhood(
                payload.get("legs") or [],
                bankroll=payload.get("bankroll"),
            )
            return _response(200, {"version": VERSION, **result})
    except (ExchangeArbError, StakeRoundingError, TypeError, ValueError) as exc:
        return _response(400, {
            "ok": False,
            "error": type(exc).__name__,
            "detail": str(exc)[:240],
            "version": VERSION,
            "places_bets": False,
        })

    return _response(404, {"ok": False, "error": "NOT_FOUND", "version": VERSION})
