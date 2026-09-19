from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict

from arb_engine import ArbValidationError, scan_all
from audit_store import enabled as audit_enabled, recent as audit_recent, record as audit_record
from constraints import apply_book_constraints, optimize_equal_payout
from lifecycle import outcome_pnl, recommend_two_leg_completion, record_leg
from market_catalog import MARKET_FAMILY_KEYS, expand_market_families
from market_discovery import discover_event_market_keys, discover_events, fetch_all_discovered_markets
from position_store import get as get_position, list_for_user, put as put_position
from provider import MARKET_FAMILIES, list_sports, scan_sport_payload
from quote_store import get_checkpoint, get_snapshot, list_snapshot_sports
from provider_books import catalog_summary
from rules import registry_rows, registry_size
from state_packs import licensed_books, list_packs, pack_summary
from ui_page import HTML
from validation import validate_events

VERSION = "INQSI-ARB-v3"
DEFAULT_MARKETS = "h2h,spreads,totals"
US_JURISDICTIONS = {
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "dc", "fl", "ga",
    "hi", "id", "il", "in", "ia", "ks", "ky", "la", "me", "md", "ma",
    "mi", "mn", "ms", "mo", "mt", "ne", "nv", "nh", "nj", "nm", "ny",
    "nc", "nd", "oh", "ok", "or", "pa", "ri", "sc", "sd", "tn", "tx",
    "ut", "vt", "va", "wa", "wv", "wi", "wy",
}


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


def _default_jurisdiction() -> str:
    return (os.environ.get("ARB_DEFAULT_JURISDICTION") or "*").strip().lower() or "*"


def _regions(jurisdiction: str, explicit: str = "") -> str:
    if explicit.strip():
        return explicit.strip()
    if jurisdiction.strip().lower() in US_JURISDICTIONS:
        return os.environ.get("ARB_US_REGIONS", "us,us2")
    return os.environ.get("ARB_REGIONS", "us,us2,us_dfs,us_ex,uk,eu,fr,se,au")


def _audit_candidate(row: Dict[str, Any]) -> Dict[str, Any]:
    """Return bounded, decision-complete candidate evidence for DynamoDB."""
    def bounded(value: Any, maximum: int = 256) -> Any:
        if isinstance(value, str):
            return value[:maximum]
        if isinstance(value, list):
            return [bounded(item, 128) for item in value[:25]]
        if isinstance(value, dict):
            return {str(key)[:64]: bounded(item) for key, item in list(value.items())[:25]}
        return value

    validation = {
        str(key): bounded(value)
        for key, value in dict(row.get("validation") or {}).items()
    }
    settlement = dict((row.get("context") or {}).get("settlement_validation") or {})
    if settlement.get("reason") and not validation.get("settlement_reason"):
        validation["settlement_reason"] = settlement["reason"]
    if settlement.get("missing_books") and not validation.get("missing_books"):
        validation["missing_books"] = bounded(settlement["missing_books"])
    all_legs = list(row.get("legs") or [])
    legs = []
    for leg in all_legs[:4]:
        legs.append({key: bounded(leg.get(key)) for key in (
            "outcome", "book", "american", "decimal", "net_decimal", "stake",
            "payout_if_wins", "profit_if_wins", "last_update", "provider", "link", "limit", "point",
        )})
    return {
        key: bounded(row.get(key)) for key in (
            "market_id", "event", "market", "commence_time", "math_arb", "arb",
            "executable", "kind", "gap", "sum_implied", "margin_pct", "hold_pct", "bankroll", "minimum_payout",
            "minimum_profit", "n_quotes", "n_books", "outcomes", "books",
        )
    } | {
        "validation": validation,
        "legs": legs,
        "n_legs": len(all_legs),
        "omitted_legs": max(0, len(all_legs) - len(legs)),
    }


def _audit_scan_payload(result: Dict[str, Any], *, sport: str, jurisdiction: str) -> Dict[str, Any]:
    def bounded(value: Any, maximum: int = 256) -> Any:
        if isinstance(value, str):
            return value[:maximum]
        if isinstance(value, list):
            return [bounded(item, 128) for item in value[:25]]
        if isinstance(value, dict):
            return {str(key)[:64]: bounded(item) for key, item in list(value.items())[:25]}
        return value

    def audit_snapshot(value: Any) -> Any:
        if not isinstance(value, dict) or not isinstance(value.get("sports"), list):
            return bounded(value)
        sports = list(value.get("sports") or [])
        compact = []
        for row in sports:
            if not isinstance(row, dict):
                continue
            head = dict(row.get("head") or {})
            compact.append({
                "sport": str(row.get("sport") or head.get("sport") or "")[:128],
                "ok": bool(row.get("ok")),
                "source": str(row.get("source") or "")[:32],
                "error": str(row.get("error") or "")[:128],
                "age_ms": row.get("age_ms"),
                "head": {
                    key: bounded(head.get(key), 128)
                    for key in (
                        "sport", "fetched_at_ms", "n_events", "n_chunks", "version",
                        "ok", "regions", "markets", "error",
                    )
                    if head.get(key) is not None
                },
            })
        output = {
            str(key)[:64]: bounded(item)
            for key, item in value.items() if key != "sports"
        }
        output["sports"] = compact
        output["n_sports_context"] = len(sports)
        output["omitted_sports_context"] = max(0, len(sports) - len(compact))
        return output

    remaining = 25
    saved: Dict[str, list] = {}
    for name in ("hits", "detected_unverified", "rejected", "exchange_pending", "middles"):
        rows = list(result.get(name) or [])
        selected = rows[:remaining]
        saved[name] = [_audit_candidate(row) for row in selected]
        remaining -= len(selected)
    counts = {
        "hits": int(result.get("n_arbs") or 0),
        "detected_unverified": int(result.get("n_detected_unverified") or 0),
        "rejected": int(result.get("n_rejected") or 0),
        "exchange_pending": int(result.get("n_exchange_pending") or 0),
        "middles": int(result.get("n_middles") or 0),
    }
    return {
        "sport": str(sport)[:256],
        "jurisdiction": str(jurisdiction)[:256],
        "source": str(result.get("source") or "")[:64],
        "regions": str(result.get("regions") or "")[:256],
        "books": bounded(result.get("books")),
        "licensed": bool(result.get("licensed")),
        "pack": bounded(result.get("pack")),
        "snapshot": audit_snapshot(result.get("status")),
        "n_markets": result.get("n_markets"),
        "n_arbs": result.get("n_arbs"),
        "n_detected_unverified": result.get("n_detected_unverified"),
        "n_rejected": result.get("n_rejected"),
        "n_held_unverified": result.get("n_held_unverified"),
        "n_exchange_pending": result.get("n_exchange_pending"),
        "n_middles": result.get("n_middles"),
        **saved,
        "truncated": {name: max(0, count - len(saved[name])) for name, count in counts.items()},
    }


def _finalize_scan(result: Dict[str, Any], *, sport: str, jurisdiction: str) -> Dict[str, Any]:
    result["version"] = VERSION
    result["jurisdiction"] = jurisdiction
    if audit_enabled():
        try:
            result["audit_event_id"] = audit_record(
                "SCAN", _audit_scan_payload(result, sport=sport, jurisdiction=jurisdiction)
            )
        except Exception as exc:
            result["audit_error"] = type(exc).__name__
    if result.get("hits"):
        result["push"] = _maybe_broadcast({
            "type": "ARB_SCAN_UPDATE", "version": VERSION,
            "sport": sport, "n_arbs": result.get("n_arbs"),
            "hits": result.get("hits", [])[:20],
        })
    return result


def _fresh_seconds() -> int:
    try:
        return max(15, min(int(os.environ.get("ARB_QUOTE_FRESH_SECONDS", "120")), 7200))
    except ValueError:
        return 120


def _licensed_book_filter(jurisdiction: str, books: str | None, licensed: bool) -> str | None:
    if not licensed:
        return books
    allowed = set(licensed_books(jurisdiction))
    if books:
        requested = {part.strip().lower() for part in books.split(",") if part.strip()}
        selected = sorted(requested & allowed)
    else:
        selected = sorted(allowed)
    # Provider requests require a comma-delimited query value. Preserve an
    # explicitly empty licensed intersection as a non-matching sentinel so it
    # can never broaden back to every book.
    return ",".join(selected) if selected else "__no_licensed_books__"


def _dimension_set(raw: Any) -> set[str]:
    values = raw.split(",") if isinstance(raw, str) else (raw or [])
    return {str(value).strip().lower() for value in values if str(value).strip()}


def _stored_event_is_open(row: Dict[str, Any], *, now: datetime | None = None) -> bool:
    """Fail closed when a cached event is missing, invalid, or has commenced."""
    raw = str(row.get("commence_time") or "").strip()
    if not raw:
        return False
    try:
        starts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if starts.tzinfo is None:
            starts = starts.replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return starts > (now or datetime.now(timezone.utc))


def _stored_events(sport: str, *, markets: list[str], regions: str) -> tuple[list | None, dict]:
    snap = get_snapshot(sport, max_age_seconds=_fresh_seconds())
    if not snap:
        return None, {"ok": False, "source": "store", "error": "QUOTE_SNAPSHOT_MISSING", "sport": sport}
    if not snap.get("ok"):
        return None, {"ok": False, "source": "store", "error": "QUOTE_SNAPSHOT_STALE" if snap.get("stale") else "QUOTE_SNAPSHOT_INCOMPLETE", "sport": sport, "head": snap.get("head")}
    head = dict(snap.get("head") or {})
    wanted_markets = _dimension_set(markets)
    stored_markets = _dimension_set(head.get("markets"))
    wanted_regions = _dimension_set(regions)
    stored_regions = _dimension_set(head.get("regions"))
    # Stored rows carry no per-quote source-region tag, so a multi-region
    # snapshot cannot be safely narrowed after retrieval.
    if not wanted_markets <= stored_markets or wanted_regions != stored_regions:
        return None, {
            "ok": False, "source": "store", "error": "QUOTE_SNAPSHOT_DIMENSION_MISMATCH",
            "sport": sport, "head": head, "requested_markets": sorted(wanted_markets),
            "requested_regions": sorted(wanted_regions),
        }
    events = [
        row for row in (snap.get("events") or [])
        if str(row.get("market") or "").lower() in wanted_markets and _stored_event_is_open(row)
    ]
    return events, {"ok": True, "source": "store", "sport": sport, "head": head, "age_ms": snap.get("age_ms")}


def lambda_handler(event, context):
    event = event or {}
    method, path, query = _method(event), _path(event), _qs(event)
    if method == "OPTIONS":
        return response(200, {"ok": True})

    if method == "GET" and path == "/v1/arb/ui":
        ws_url = os.environ.get("ARB_WEBSOCKET_PUBLIC_URL", "").strip()
        return response(200, HTML.replace("__INQSI_WS_URL__", ws_url), content_type="text/html; charset=utf-8")

    if method == "GET" and path == "/v1/arb/health":
        checkpoint = None
        checkpoint_error = None
        try:
            checkpoint = get_checkpoint() or None
        except Exception as exc:
            checkpoint_error = type(exc).__name__
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
            "websocket_public_url_present": bool(os.environ.get("ARB_WEBSOCKET_PUBLIC_URL")),
            "balance_aware_optimizer": True,
            "two_leg_completion_assistant": True,
            "opportunity_history": audit_enabled(),
            "default_jurisdiction": _default_jurisdiction(),
            "candidate_evidence_audit": True,
            "required_outcome_universe_preserved": True,
            "exchange_lay_routed": True,
            "middle_detection": True,
            "executable_rounding": True,
            "user_book_filter": True,
            "state_packs": True,
            "quote_collector": True,
            "provider_book_catalog": True,
            "live_settlement_states": True,
            "sportsbook_scope": "all_provider_returned",
            "collector_checkpoint": checkpoint,
            "collector_checkpoint_error": checkpoint_error,
            "default_regions": _regions(_default_jurisdiction()).split(","),
        })

    if method == "GET" and path == "/v1/arb/history":
        if not audit_enabled():
            return response(503, {"ok": False, "error": "AUDIT_PERSISTENCE_UNAVAILABLE", "version": VERSION})
        try:
            limit = max(1, min(int(query.get("limit", "20")), 50))
        except ValueError:
            return response(400, {"ok": False, "error": "INVALID_LIMIT"})
        rows = audit_recent("SCAN", limit=limit)
        return response(200, {
            "ok": True,
            "version": VERSION,
            "kind": "SCAN",
            "count": len(rows),
            "history": rows,
            "policy": "Read-only scan/opportunity history; user-specific position audit events are not exposed by this endpoint.",
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

    if method == "GET" and path == "/v1/arb/books":
        return response(200, {"version": VERSION, **catalog_summary()})

    if method == "GET" and path == "/v1/arb/collector":
        checkpoint = None
        checkpoint_error = None
        try:
            checkpoint = get_checkpoint() or None
        except Exception as exc:
            checkpoint_error = type(exc).__name__
        return response(200, {
            "ok": True,
            "version": VERSION,
            "places_bets": False,
            "schedule": "rate(2 minutes)",
            "checkpoint": checkpoint,
            "checkpoint_error": checkpoint_error,
            "fresh_seconds": _fresh_seconds(),
            "policy": "Collector writes featured-market snapshots. It never places bets.",
        })

    if method == "GET" and path == "/v1/arb/packs":
        packs = list_packs()
        return response(200, {
            "ok": True,
            "version": VERSION,
            "count": len(packs),
            "packs": packs,
            "places_bets": False,
            "policy": "State packs describe licensed-book availability and reviewed settlement coverage. They do not place bets.",
        })

    if method == "GET" and path.startswith("/v1/arb/packs/"):
        state = path.rsplit("/", 1)[-1].strip().lower()
        summary = pack_summary(state)
        return response(200 if summary.get("ok") else 404, {"version": VERSION, "places_bets": False, **summary})

    if method == "GET" and path == "/v1/arb/catalog":
        sports, meta = list_sports(all_sports=query.get("all", "false").lower() == "true")
        return response(200 if meta.get("ok") else 503, {
            "ok": bool(meta.get("ok")),
            "version": VERSION,
            "sports": sports,
            "n_sports": len(sports),
            "market_families": {**MARKET_FAMILIES, **MARKET_FAMILY_KEYS},
            "sportsbook_policy": "all provider-returned books in requested configured regions; no hard-coded allowlist",
            "settlement_policy": "explicit reviewed book rules only; unknown combinations fail closed",
            "provider": meta,
        })

    if method == "GET" and path == "/v1/arb/markets":
        sport = query.get("sport", "").strip()
        event_id = query.get("event_id", "").strip()
        if not sport:
            return response(400, {"ok": False, "error": "SPORT_REQUIRED"})
        jurisdiction = (query.get("jurisdiction") or _default_jurisdiction()).strip().lower()
        regions = _regions(jurisdiction, query.get("regions", ""))
        if event_id:
            keys, meta = discover_event_market_keys(
                sport, event_id, regions=regions, bookmakers=query.get("bookmakers"),
                max_markets=int(os.environ.get("ARB_MAX_MARKETS_PER_EVENT", "120")),
            )
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

        jurisdiction = (query.get("jurisdiction") or _default_jurisdiction()).strip().lower()
        regions = _regions(jurisdiction, query.get("regions", ""))
        source = (query.get("source") or "auto").strip().lower()
        if source not in {"auto", "store", "live"}:
            return response(400, {"ok": False, "error": "INVALID_SOURCE", "source": source})
        licensed = (query.get("licensed") or "false").strip().lower() in {"1", "true", "yes"}
        books = _licensed_book_filter(
            jurisdiction,
            (query.get("books") or query.get("bookmakers") or "").strip() or None,
            licensed,
        )
        pack = pack_summary(jurisdiction)

        markets: list[str] = []
        if market_arg.lower() != "all":
            markets = [m.strip() for m in market_arg.split(",") if m.strip()]
            if families_arg:
                markets.extend(expand_market_families([x.strip() for x in families_arg.split(",") if x.strip()]))
            markets = list(dict.fromkeys(markets))
            if not markets:
                return response(400, {"ok": False, "error": "MARKETS_REQUIRED"})

        def _scan_rows(rows, status):
            rows = validate_events(rows, jurisdiction=jurisdiction)
            result = scan_all({"bankroll": bankroll, "events": rows, "books": books})
            result["status"] = status
            result["pack"] = pack
            result["source"] = status.get("source") or source
            result["regions"] = regions
            result["books"] = books
            result["licensed"] = licensed
            return response(200 if status.get("ok") else 503, _finalize_scan(result, sport=sport, jurisdiction=jurisdiction))

        if source == "store":
            if market_arg.lower() == "all":
                return response(400, {"ok": False, "error": "STORE_ALL_MARKETS_UNSUPPORTED"})
            if sport == "all":
                active_inventory = {
                    str(key) for key in (get_checkpoint().get("active_sports") or []) if str(key)
                }
                if active_inventory:
                    # The checkpoint is the completeness contract. Iterating
                    # existing HEAD rows would silently omit an active sport
                    # whose first/most-recent collector write failed.
                    keys = sorted(active_inventory)
                else:
                    keys = list_snapshot_sports()
                if not keys:
                    return response(503, {"ok": False, "error": "QUOTE_SNAPSHOT_UNAVAILABLE"})
                combined_events = []
                statuses = []
                for key in keys:
                    events, status = _stored_events(key, markets=markets, regions=regions)
                    statuses.append(status)
                    if events is not None:
                        combined_events.extend(events)
                if not all(status.get("ok") for status in statuses):
                    return response(503, {"ok": False, "error": "QUOTE_SNAPSHOT_INCOMPLETE", "sports": statuses})
                return _scan_rows(combined_events, {
                    "ok": True, "source": "store", "sports": statuses,
                    "n_sports_scanned": len(statuses),
                })
            else:
                events, status = _stored_events(sport, markets=markets, regions=regions)
                if events is not None:
                    return _scan_rows(events, status)
                return response(503, {"ok": False, "error": status.get("error") or "QUOTE_SNAPSHOT_UNAVAILABLE", "status": status})

        catalog_cache = None
        if source == "auto" and market_arg.lower() != "all":
            if sport == "all":
                sports, sports_meta = list_sports(all_sports=False)
                catalog_cache = (sports, sports_meta)
                combined_events = []
                statuses = []
                cache_complete = True
                if sports_meta.get("ok"):
                    limit = min(len(sports), int(os.environ.get("ARB_MAX_SPORTS_PER_ALL_SCAN", "100")))
                    for row in sports[:limit]:
                        events, status = _stored_events(row["key"], markets=markets, regions=regions)
                        statuses.append(status)
                        if events:
                            combined_events.extend(events)
                        elif events is None or int((status.get("head") or {}).get("n_events") or 0) != 0:
                            cache_complete = False
                if (
                    sports_meta.get("ok") and statuses and cache_complete
                    and all(status.get("ok") for status in statuses)
                ):
                    return _scan_rows(combined_events, {
                        "ok": True, "source": "store", "sports": statuses,
                        "n_sports_scanned": len(statuses), "catalog": sports_meta,
                    })
            else:
                events, status = _stored_events(sport, markets=markets, regions=regions)
                if events or (
                    events is not None
                    and int((status.get("head") or {}).get("n_events") or 0) == 0
                ):
                    return _scan_rows(events, status)

        if market_arg.lower() == "all":
            rows, status = fetch_all_discovered_markets(
                sport,
                regions=regions,
                bookmakers=books,
                max_events=max_events,
                max_markets_per_event=int(os.environ.get("ARB_MAX_MARKETS_PER_EVENT", "120")),
            )
            status = {**status, "source": "live"}
            return _scan_rows(rows, status)

        if sport == "all":
            sports, sports_meta = catalog_cache or list_sports(all_sports=False)
            if not sports_meta.get("ok"):
                return response(503, {"ok": False, "error": "SPORT_CATALOG_UNAVAILABLE", "provider": sports_meta})
            limit = min(len(sports), int(os.environ.get("ARB_MAX_SPORTS_PER_ALL_SCAN", "100")))
            combined_events = []
            statuses = []
            for row in sports[:limit]:
                payload = scan_sport_payload(
                    row["key"], bankroll=bankroll, markets=markets,
                    regions=regions, bookmakers=books, max_events=max_events,
                )
                combined_events.extend(payload["events"])
                statuses.append(payload["status"])
            return _scan_rows(combined_events, {
                "ok": True, "source": "live", "sports": statuses,
                "n_sports_scanned": limit, "catalog": sports_meta,
            })

        payload = scan_sport_payload(
            sport, bankroll=bankroll, markets=markets,
            regions=regions, bookmakers=books, max_events=max_events,
        )
        return _scan_rows(payload["events"], {**(payload.get("status") or {}), "source": "live"})

    if method == "POST" and path in {"/v1/arb/scan", "/v1/scan"}:
        payload = _body(event)
        if not isinstance(payload.get("events"), list):
            return response(400, {"ok": False, "error": "BODY_EVENTS_REQUIRED"})
        jurisdiction = str(payload.get("jurisdiction") or _default_jurisdiction()).strip().lower()
        payload["events"] = validate_events(payload["events"], jurisdiction=jurisdiction)
        try:
            result = scan_all(payload)
        except (ValueError, ArbValidationError) as exc:
            return response(400, {"ok": False, "error": "INVALID_SCAN_PAYLOAD", "detail": str(exc)[:200]})
        result["status"] = {"source": "posted", "places_bets": False}
        return response(200, _finalize_scan(
            result, sport=str(payload.get("sport") or "posted"), jurisdiction=jurisdiction
        ))

    if method == "POST" and path == "/v1/arb/constraints":
        payload = _body(event)
        result = apply_book_constraints(payload.get("legs") or [], payload.get("profiles") or {})
        return response(200, {"ok": True, "version": VERSION, **result})

    if method == "POST" and path == "/v1/arb/optimize":
        payload = _body(event)
        try:
            result = optimize_equal_payout(
                payload.get("legs") or [], payload.get("profiles") or {},
                bankroll=float(payload.get("bankroll") or 0),
            )
        except (TypeError, ValueError) as exc:
            return response(400, {"ok": False, "error": "INVALID_OPTIMIZATION_PAYLOAD", "detail": str(exc)[:200]})
        return response(200, {"ok": True, "version": VERSION, "places_bets": False, **result})

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
                if len(set(str(x) for x in position["required_outcomes"] if str(x))) < 2:
                    return response(400, {"ok": False, "error": "AT_LEAST_TWO_REQUIRED_OUTCOMES"})
                put_position(user_id, position_id, position)
                audit_record("POSITION_CREATED", {"user_id": user_id, "position_id": position_id, "market_id": position["market_id"]})
                return response(201, {"ok": True, "position": position})
            if method == "POST" and position_id and path.endswith("/complete"):
                pos = get_position(user_id, position_id)
                if not pos:
                    return response(404, {"ok": False, "error": "POSITION_NOT_FOUND"})
                payload = _body(event)
                recommendation = recommend_two_leg_completion(
                    pos, payload.get("candidate_quotes") or [], payload.get("profiles") or {},
                )
                audit_record("POSITION_COMPLETION_RECOMMENDED", {
                    "user_id": user_id,
                    "position_id": position_id,
                    "n_recommendations": recommendation.get("n_recommendations"),
                    "n_feasible": recommendation.get("n_feasible"),
                })
                return response(200, {"version": VERSION, **recommendation})
            if method == "POST" and position_id and path.endswith("/legs"):
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
