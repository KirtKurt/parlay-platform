from __future__ import annotations

import json
import os
from decimal import Decimal
from typing import Any, Dict

from arb_engine import ArbValidationError, scan_all
from provider import MARKET_FAMILIES, list_sports, scan_sport_payload

VERSION = "INQSI-ARB-v2"
DEFAULT_MARKETS = "h2h,spreads,totals"


def response(status: int, body: Any) -> Dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json",
            "access-control-allow-origin": "*",
            "access-control-allow-headers": "content-type",
            "access-control-allow-methods": "GET,POST,OPTIONS",
            "cache-control": "no-store",
        },
        "body": json.dumps(body, default=lambda v: float(v) if isinstance(v, Decimal) else str(v)),
    }


def _method(event: Dict[str, Any]) -> str:
    return str(event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method") or "").upper()


def _path(event: Dict[str, Any]) -> str:
    return str(event.get("rawPath") or event.get("path") or "/")


def _qs(event: Dict[str, Any]) -> Dict[str, str]:
    return {str(k): str(v) for k, v in (event.get("queryStringParameters") or {}).items() if v is not None}


def _body(event: Dict[str, Any]) -> Dict[str, Any]:
    try:
        value = json.loads(event.get("body") or "{}")
        return value if isinstance(value, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def lambda_handler(event, context):
    event = event or {}
    method, path, query = _method(event), _path(event), _qs(event)
    if method == "OPTIONS":
        return response(200, {"ok": True})

    if method == "GET" and path == "/v1/arb/health":
        return response(200, {
            "ok": True,
            "service": "inqsi-arb",
            "version": VERSION,
            "places_bets": False,
            "provider_key_present": bool(os.environ.get("ODDS_API_KEY") or os.environ.get("THE_ODDS_API_KEY")),
        })

    if method == "GET" and path == "/v1/arb/catalog":
        sports, meta = list_sports(all_sports=query.get("all", "false").lower() == "true")
        return response(200 if meta.get("ok") else 503, {
            "ok": bool(meta.get("ok")),
            "version": VERSION,
            "sports": sports,
            "n_sports": len(sports),
            "market_families": MARKET_FAMILIES,
            "sportsbook_policy": "all provider-returned books in requested permitted regions; no hard-coded allowlist",
            "provider": meta,
        })

    if method == "GET" and path == "/v1/arb/scan":
        sport = query.get("sport", "baseball_mlb").strip()
        markets = [m.strip() for m in query.get("markets", DEFAULT_MARKETS).split(",") if m.strip()]
        try:
            bankroll = float(query.get("bankroll", "1000"))
            max_events = int(query.get("max_events", os.environ.get("ARB_MAX_EVENT_MARKET_EVENTS", "40")))
        except ValueError:
            return response(400, {"ok": False, "error": "INVALID_NUMERIC_PARAMETER"})
        if not markets:
            return response(400, {"ok": False, "error": "MARKETS_REQUIRED"})
        if sport == "all":
            sports, sports_meta = list_sports(all_sports=False)
            if not sports_meta.get("ok"):
                return response(503, {"ok": False, "error": "SPORT_CATALOG_UNAVAILABLE", "provider": sports_meta})
            limit = min(len(sports), int(os.environ.get("ARB_MAX_SPORTS_PER_ALL_SCAN", "100")))
            combined = {"bankroll": bankroll, "events": []}
            statuses = []
            for row in sports[:limit]:
                payload = scan_sport_payload(
                    row["key"], bankroll=bankroll, markets=markets,
                    regions=query.get("regions"), bookmakers=query.get("bookmakers"), max_events=max_events,
                )
                combined["events"].extend(payload["events"])
                statuses.append(payload["status"])
            result = scan_all(combined)
            result.update({"version": VERSION, "status": {"sports": statuses, "n_sports_scanned": limit, "catalog": sports_meta}})
            return response(200, result)
        payload = scan_sport_payload(
            sport,
            bankroll=bankroll,
            markets=markets,
            regions=query.get("regions"),
            bookmakers=query.get("bookmakers"),
            max_events=max_events,
        )
        result = scan_all(payload)
        result.update({"version": VERSION, "status": payload["status"]})
        return response(200 if payload["status"].get("ok") else 503, result)

    if method == "POST" and path in {"/v1/arb/scan", "/v1/scan"}:
        payload = _body(event)
        if not isinstance(payload.get("events"), list):
            return response(400, {"ok": False, "error": "BODY_EVENTS_REQUIRED"})
        try:
            result = scan_all(payload)
        except (ValueError, ArbValidationError) as exc:
            return response(400, {"ok": False, "error": "INVALID_SCAN_PAYLOAD", "detail": str(exc)[:200]})
        result.update({"version": VERSION, "status": {"source": "posted", "places_bets": False}})
        return response(200, result)

    return response(404, {"ok": False, "error": "NOT_FOUND", "version": VERSION})
