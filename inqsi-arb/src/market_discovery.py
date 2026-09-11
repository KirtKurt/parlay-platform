"""Automatic provider event/market discovery for Inqsi ARB.

The scanner asks the provider what markets exist for each event, then requests
those concrete keys. Unsupported discovery fails closed instead of pretending a
fixed catalog is complete.
"""
from __future__ import annotations

import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

from provider import BASE, _get, api_key, normalize_games


def discover_events(sport_key: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    key = api_key()
    if not key:
        return [], {"ok": False, "error": "ODDS_API_KEY_MISSING"}
    payload, meta = _get(
        f"{BASE}/sports/{urllib.parse.quote(str(sport_key), safe='')}/events/",
        {"apiKey": key, "dateFormat": "iso"},
    )
    if not meta.get("ok") or not isinstance(payload, list):
        return [], meta
    rows = [x for x in payload if isinstance(x, dict) and x.get("id")]
    return rows, {**meta, "n_events": len(rows)}


def discover_event_market_keys(sport_key: str, event_id: str) -> Tuple[List[str], Dict[str, Any]]:
    key = api_key()
    if not key:
        return [], {"ok": False, "error": "ODDS_API_KEY_MISSING"}
    url = (
        f"{BASE}/sports/{urllib.parse.quote(str(sport_key), safe='')}"
        f"/events/{urllib.parse.quote(str(event_id), safe='')}/markets"
    )
    payload, meta = _get(url, {"apiKey": key})
    if not meta.get("ok"):
        return [], {**meta, "discovery": "provider_event_markets"}
    keys: List[str] = []
    seen = set()
    rows = payload if isinstance(payload, list) else (payload.get("markets", []) if isinstance(payload, dict) else [])
    for row in rows:
        market_key = str(row.get("key") if isinstance(row, dict) else row or "").strip()
        if market_key and market_key not in seen:
            seen.add(market_key)
            keys.append(market_key)
    return keys, {**meta, "discovery": "provider_event_markets", "n_market_keys": len(keys)}


def discover_sport_event_markets(sport_key: str, events: List[Dict[str, Any]], *, max_events: int = 40) -> Dict[str, Any]:
    discovered: Dict[str, List[str]] = {}
    errors: List[Dict[str, Any]] = []
    selected = (events or [])[:max(0, int(max_events))]
    for event in selected:
        event_id = str(event.get("id") or "").strip()
        if not event_id:
            continue
        keys, meta = discover_event_market_keys(sport_key, event_id)
        if meta.get("ok"):
            discovered[event_id] = keys
        else:
            errors.append({"event_id": event_id, "error": meta.get("error"), "status": meta.get("status")})
    return {
        "ok": bool(discovered) and len(errors) < max(1, len(selected)),
        "sport": sport_key,
        "events": discovered,
        "n_events": len(discovered),
        "unique_markets": sorted({k for values in discovered.values() for k in values}),
        "errors": errors[:20],
    }


def fetch_all_discovered_markets(
    sport_key: str,
    *,
    regions: str,
    bookmakers: Optional[str] = None,
    max_events: int = 40,
    max_markets_per_event: int = 80,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Enumerate event-specific markets and fetch every discovered key.

    Provider quotas are protected by explicit event/market caps. Returned status
    reports truncation so the UI never presents partial enumeration as complete.
    """
    key = api_key()
    events, events_meta = discover_events(sport_key)
    if not events_meta.get("ok"):
        return [], {"ok": False, "stage": "events", "events": events_meta}
    selected = events[:max(0, int(max_events))]
    normalized: List[Dict[str, Any]] = []
    details: List[Dict[str, Any]] = []
    for event in selected:
        event_id = str(event["id"])
        keys, discovery_meta = discover_event_market_keys(sport_key, event_id)
        if not discovery_meta.get("ok"):
            details.append({"event_id": event_id, "ok": False, "discovery": discovery_meta})
            continue
        truncated = len(keys) > max_markets_per_event
        requested = keys[:max(0, int(max_markets_per_event))]
        if not requested:
            details.append({"event_id": event_id, "ok": True, "n_discovered": 0, "n_requested": 0})
            continue
        url = (
            f"{BASE}/sports/{urllib.parse.quote(str(sport_key), safe='')}"
            f"/events/{urllib.parse.quote(event_id, safe='')}/odds"
        )
        payload, odds_meta = _get(url, {
            "apiKey": key,
            "regions": regions,
            "markets": ",".join(requested),
            "bookmakers": bookmakers,
            "oddsFormat": "american",
            "dateFormat": "iso",
            "includeLinks": "true",
            "includeBetLimits": "true",
        })
        ok = bool(odds_meta.get("ok") and isinstance(payload, dict))
        if ok:
            normalized.extend(normalize_games([payload], sport_key=sport_key))
        details.append({
            "event_id": event_id,
            "ok": ok,
            "n_discovered": len(keys),
            "n_requested": len(requested),
            "truncated": truncated,
            "odds": odds_meta,
        })
    good = sum(1 for x in details if x.get("ok"))
    return normalized, {
        "ok": good > 0,
        "stage": "complete",
        "n_events_discovered": len(events),
        "n_events_requested": len(selected),
        "n_events_succeeded": good,
        "n_normalized_markets": len(normalized),
        "event_details": details[:100],
        "partial": len(selected) < len(events) or any(x.get("truncated") for x in details),
    }
