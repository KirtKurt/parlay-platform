"""Automatic provider market discovery for Inqsi ARB.

Uses the provider event-markets endpoint when available. If the provider does not
support discovery for an event, callers receive an explicit fail-closed result.
"""
from __future__ import annotations

import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

from provider import BASE, _get, api_key


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
    for event in (events or [])[:max(0, int(max_events))]:
        event_id = str(event.get("id") or "").strip()
        if not event_id:
            continue
        keys, meta = discover_event_market_keys(sport_key, event_id)
        if meta.get("ok"):
            discovered[event_id] = keys
        else:
            errors.append({"event_id": event_id, "error": meta.get("error"), "status": meta.get("status")})
    return {
        "ok": bool(discovered) and not (len(errors) == len((events or [])[:max_events])),
        "sport": sport_key,
        "events": discovered,
        "n_events": len(discovered),
        "unique_markets": sorted({k for values in discovered.values() for k in values}),
        "errors": errors[:20],
    }
