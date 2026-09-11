from __future__ import annotations

import json
import os
import uuid
from decimal import Decimal
from typing import Any, Dict

from arb_engine import ArbValidationError, scan_all
from audit_store import enabled as audit_enabled, record as audit_record
from constraints import apply_book_constraints
from lifecycle import outcome_pnl, record_leg
from market_catalog import MARKET_FAMILY_KEYS, expand_market_families
from market_discovery import discover_event_market_keys, discover_events, fetch_all_discovered_markets
from position_store import get as get_position, list_for_user, put as put_position
from provider import MARKET_FAMILIES, list_sports, scan_sport_payload
from rules import registry_rows, registry_size
from ui_page import HTML
from validation import validate_events

VERSION = "INQSI-ARB-v3"
DEFAULT_MARKETS = "h2h,spreads,totals"


def response(status: int, body: Any, *, content_type: str = "application/json") -> Dict[str, Any]:
    payload = body if isinstance(body, str) and content_type != "application/json" else json.dumps(
        body, default=lambda v: float(v) if isinstance(v, Decimal) else str(v)
    )
    return {
        "statusCode": status,
        "headers": {
            "content-type": content_type,
            "access-control-allow-origin": "*",
            "access-control-allow-headers": "content-type,x-inqsi-user-id",
            "access-control-allow-methods": "GET,POST,OPTIONS",
            "cache-control": "no-store",
        },
        "body": payload,
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


def _user(event: Dict[str, Any]) -> str:
    headers = {str(k).lower(): str(v) for k, v in (event.get("headers") or {}).items()}
    return (headers.get("x-inqsi-user-id") or "anonymous").strip()[:128] or "anonymous"


def _maybe_broadcast(payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        from websocket import broadcast
        return broadcast(payload)
    except Exception as exc:
        return {"sent": 0, "stale": 0, "error": type(exc).__name__}


def _finalize_scan(result: Dict[str, Any], *, sport: str) -> Dict[str, Any]:
    result["version"] = VERSION
    if audit_enabled():
        try:
            result["audit_event_id"] = audit_record("SCAN", {
                "sport": sport,
                "n_markets": result.get("n_markets"),
                "n_arbs": result.get("n_arbs"),
                "n_rejected": result.get("n_rejected"),
                "hits": result.get("hits", [])[:50],
            })
        except Exception as exc:
            result["audit_error"] = type(exc).__name__
    if result.get("hits"):
        result["push"] = _maybe_broadcast({
            "type": "ARB_SCAN_UPDATE", "version": VERSION,
            "sport": sport, "n_arbs": result.get("n_arbs"),
            "hits": result.get("hits", [])[:20],
        })
    return result


def lambda_handler(event, context):
    event = event or {}
    method, path, query = _method(event), _path(event), _qs(event)
    if method == "OPTIONS":
        return response(200, {"ok": True})

    if method == "GET" and path == "/v1/arb/ui":
        return response(200, HTML, content_type="text/html; charset=utf-8")

    if method == "GET" and path == "/v1/arb/health":
        return response(200, {
            "ok": True,
            "service": "inqsi-arb",
            "version": VERSION,
            "places_bets": False,
            "provider_key_present": bool(os.environ.get("ODDS_API_KEY") or os.environ.get("THE_ODDS_API_KEY")),
            "audit_persistence": audit_enabled(),
            "rules_registry_entries": registry_size(),
            "automatic_market_discovery": True,
            "websocket_push_configured": bool(os.environ.get("ARB_WEBSOCKET_MANAGEMENT_ENDPOINT")),
        })

    if method == "GET" and path == "/v1/arb/rules":
        rows = registry_rows()
        book_filter = query.get("book", "").strip().lower()
        sport_filter = query.get("sport", "").strip().lower()
        market_filter = query.get("market_family", "").strip().lower()
        if book_filter:
            rows = [r for r in rows if r.get("book") == book_filter]
        if sport_filter:
            rows = [r for r in rows if r.get("sport") == sport_filter]
        if market_filter:
            rows = [r for r in rows if r.get("market_family") == market_filter]
        return response(200, {
            "ok": True,
            "version": VERSION,
            "count": len(rows),
            "rules": rows,
            "policy": "Only reviewed exact-book rule combinations may qualify verified arbs; all others fail closed.",
        })

    if method == "GET" and path == "/v1/arb/catalog":
        sports, meta = list_sports(all_sports=query.get("all", "false").lower() == "true")
        return response(200 if meta.get("ok") else 503, {
            "ok": bool(meta.get("ok")),
            "version": VERSION,
            "sports": sports,
            "n_sports": len(sports),
            "market_families": {**MARKET_FAMILIES, **MARKET_FAMILY_KEYS},
            "sportsbook_policy": "all provider-returned books in requested permitted regions; no hard-coded allowlist",
            "settlement_policy": "explicit reviewed book rules only; unknown combinations fail closed",
            "provider": meta,
        })

    if method == "GET" and path == "/v1/arb/markets":
        sport = query.get("sport", "").strip()
        event_id = query.get("event_id", "").strip()
        if not sport:
            return response(400, {"ok": False, "error": "SPORT_REQUIRED"})
        if event_id:
            keys, meta = discover_event_market_keys(sport, event_id)
            return response(200 if meta.get("ok") else 503, {"ok": bool(meta.get("ok")), "sport": sport, "event_id": event_id, "markets": keys, "provider": meta})
        events, meta = discover_events(sport)
        return response(200 if meta.get("ok") else 503, {"ok": bool(meta.get("ok")), "sport": sport, "events": events, "provider": meta})

    if method == "GET" and path == "/v1/arb/scan":
        sport = query.get("sport", "baseball_mlb").strip()
        market_arg = query.get("markets", DEFAULT_MARKETS).strip()
        families_arg = query.get("families", "").strip()
        try:
            bankroll = float(query.get("bankroll", "1000"))
            max_events = int(query.get("max_events", os.environ.get("ARB_MAX_EVENT_MARKET_EVENTS", "40")))
        except ValueError:
            return response(400, {"ok": False, "error": "INVALID_NUMERIC_PARAMETER"})

        jurisdiction = query.get("jurisdiction", "*")
        if market_arg.lower() == "all":
            rows, status = fetch_all_discovered_markets(
                sport,
                regions=query.get("regions") or os.environ.get("ARB_REGIONS", "us,us2,uk,eu,au"),
                bookmakers=query.get("bookmakers"),
                max_events=max_events,
                max_markets_per_event=int(os.environ.get("ARB_MAX_MARKETS_PER_EVENT", "80")),
            )
            rows = validate_events(rows, jurisdiction=jurisdiction)
            result = scan_all({"bankroll": bankroll, "events": rows})
            result["status"] = status
            return response(200 if status.get("ok") else 503, _finalize_scan(result, sport=sport))

        markets = [m.strip() for m in market_arg.split(",") if m.strip()]
        if families_arg:
            markets.extend(expand_market_families([x.strip() for x in families_arg.split(",") if x.strip()]))
        markets = list(dict.fromkeys(markets))
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
                combined["events"].extend(validate_events(payload["events"], jurisdiction=jurisdiction))
                statuses.append(payload["status"])
            result = scan_all(combined)
            result["status"] = {"sports": statuses, "n_sports_scanned": limit, "catalog": sports_meta}
            return response(200, _finalize_scan(result, sport="all"))

        payload = scan_sport_payload(
            sport, bankroll=bankroll, markets=markets,
            regions=query.get("regions"), bookmakers=query.get("bookmakers"), max_events=max_events,
        )
        payload["events"] = validate_events(payload["events"], jurisdiction=jurisdiction)
        result = scan_all(payload)
        result["status"] = payload["status"]
        return response(200 if payload["status"].get("ok") else 503, _finalize_scan(result, sport=sport))

    if method == "POST" and path in {"/v1/arb/scan", "/v1/scan"}:
        payload = _body(event)
        if not isinstance(payload.get("events"), list):
            return response(400, {"ok": False, "error": "BODY_EVENTS_REQUIRED"})
        payload["events"] = validate_events(payload["events"], jurisdiction=str(payload.get("jurisdiction") or "*"))
        try:
            result = scan_all(payload)
        except (ValueError, ArbValidationError) as exc:
            return response(400, {"ok": False, "error": "INVALID_SCAN_PAYLOAD", "detail": str(exc)[:200]})
        result["status"] = {"source": "posted", "places_bets": False}
        return response(200, _finalize_scan(result, sport=str(payload.get("sport") or "posted")))

    if method == "POST" and path == "/v1/arb/constraints":
        payload = _body(event)
        result = apply_book_constraints(payload.get("legs") or [], payload.get("profiles") or {})
        return response(200, {"ok": True, "version": VERSION, **result})

    if path.startswith("/v1/arb/positions"):
        user_id = _user(event)
        if user_id == "anonymous":
            return response(401, {"ok": False, "error": "INQSI_USER_ID_REQUIRED"})
        parts = [x for x in path.split("/") if x]
        position_id = parts[3] if len(parts) >= 4 else ""
        try:
            if method == "GET" and not position_id:
                return response(200, {"ok": True, "positions": list_for_user(user_id, int(query.get("limit", "50")))})
            if method == "GET" and position_id:
                pos = get_position(user_id, position_id)
                return response(200 if pos else 404, {"ok": bool(pos), "position": pos})
            if method == "POST" and not position_id:
                payload = _body(event)
                position_id = str(payload.get("position_id") or uuid.uuid4().hex)
                position = {
                    "position_id": position_id,
                    "state": "REVIEWED",
                    "market_id": str(payload.get("market_id") or ""),
                    "required_outcomes": list(payload.get("required_outcomes") or []),
                    "accepted_legs": [],
                }
                put_position(user_id, position_id, position)
                audit_record("POSITION_CREATED", {"user_id": user_id, "position_id": position_id})
                return response(201, {"ok": True, "position": position})
            if method == "POST" and position_id and path.endswith("/legs"):
                position_id = parts[3]
                pos = get_position(user_id, position_id)
                if not pos:
                    return response(404, {"ok": False, "error": "POSITION_NOT_FOUND"})
                updated = record_leg(pos, _body(event))
                updated["outcome_pnl"] = outcome_pnl(updated)
                put_position(user_id, position_id, updated)
                audit_record("POSITION_UPDATED", {"user_id": user_id, "position_id": position_id, "state": updated.get("state")})
                return response(200, {"ok": True, "position": updated})
        except (ValueError, RuntimeError) as exc:
            return response(400, {"ok": False, "error": type(exc).__name__, "detail": str(exc)[:200]})

    return response(404, {"ok": False, "error": "NOT_FOUND", "version": VERSION})
