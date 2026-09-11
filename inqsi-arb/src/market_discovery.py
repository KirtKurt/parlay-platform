"""Automatic event-market enumeration for Inqsi ARB.

The provider documents event-specific market keys but does not expose a complete
per-event market-list endpoint. Inqsi therefore builds the sport-appropriate
provider key universe from the documented catalog and probes the event-odds
endpoint. Unsupported keys are isolated by recursive bisection and fail closed.
"""
from __future__ import annotations

import urllib.parse
from typing import Any, Dict, List, Optional, Sequence, Tuple

from market_catalog import candidate_markets_for_sport
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


def _event_odds_url(sport_key: str, event_id: str) -> str:
    return (
        f"{BASE}/sports/{urllib.parse.quote(str(sport_key), safe='')}"
        f"/events/{urllib.parse.quote(str(event_id), safe='')}/odds"
    )


def _request_event_odds(
    sport_key: str,
    event_id: str,
    markets: Sequence[str],
    *,
    regions: str,
    bookmakers: Optional[str] = None,
) -> Tuple[Any, Dict[str, Any]]:
    return _get(_event_odds_url(sport_key, event_id), {
        "apiKey": api_key(),
        "regions": regions,
        "markets": ",".join(markets),
        "bookmakers": bookmakers,
        "oddsFormat": "american",
        "dateFormat": "iso",
        "includeLinks": "true",
        "includeBetLimits": "true",
    })


def _probe_market_batch(
    sport_key: str,
    event_id: str,
    markets: Sequence[str],
    *,
    regions: str,
    bookmakers: Optional[str],
    depth: int = 0,
) -> Tuple[List[str], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Return accepted keys, normalized rows, and probe evidence.

    Successful responses reveal which requested markets actually returned data.
    Invalid-market responses are bisected until unsupported singleton keys can be
    rejected without discarding valid neighbors.
    """
    if not markets:
        return [], [], []
    payload, meta = _request_event_odds(sport_key, event_id, markets, regions=regions, bookmakers=bookmakers)
    evidence = [{"markets": list(markets), "ok": bool(meta.get("ok")), "status": meta.get("status"), "error": meta.get("error"), "depth": depth}]
    if meta.get("ok") and isinstance(payload, dict):
        returned = []
        for book in payload.get("bookmakers") or []:
            for market in book.get("markets") or []:
                key = str(market.get("key") or "").strip()
                if key and key not in returned:
                    returned.append(key)
        return returned, normalize_games([payload], sport_key=sport_key), evidence
    if len(markets) == 1:
        return [], [], evidence
    midpoint = len(markets) // 2
    left = _probe_market_batch(sport_key, event_id, markets[:midpoint], regions=regions, bookmakers=bookmakers, depth=depth + 1)
    right = _probe_market_batch(sport_key, event_id, markets[midpoint:], regions=regions, bookmakers=bookmakers, depth=depth + 1)
    accepted = list(dict.fromkeys(left[0] + right[0]))
    return accepted, left[1] + right[1], evidence + left[2] + right[2]


def discover_event_market_keys(
    sport_key: str,
    event_id: str,
    *,
    regions: str = "us,us2,uk,eu,au",
    bookmakers: Optional[str] = None,
    max_markets: int = 120,
) -> Tuple[List[str], Dict[str, Any]]:
    if not api_key():
        return [], {"ok": False, "error": "ODDS_API_KEY_MISSING"}
    candidates = candidate_markets_for_sport(sport_key)[:max(1, int(max_markets))]
    accepted, _rows, evidence = _probe_market_batch(
        sport_key, event_id, candidates, regions=regions, bookmakers=bookmakers,
    )
    return accepted, {
        "ok": bool(accepted),
        "discovery": "documented_catalog_runtime_probe",
        "n_candidates": len(candidates),
        "n_market_keys": len(accepted),
        "partial": len(candidate_markets_for_sport(sport_key)) > len(candidates),
        "probe_requests": len(evidence),
        "evidence": evidence[:50],
    }


def discover_sport_event_markets(
    sport_key: str,
    events: List[Dict[str, Any]],
    *,
    regions: str = "us,us2,uk,eu,au",
    bookmakers: Optional[str] = None,
    max_events: int = 40,
    max_markets: int = 120,
) -> Dict[str, Any]:
    discovered: Dict[str, List[str]] = {}
    errors: List[Dict[str, Any]] = []
    selected = (events or [])[:max(0, int(max_events))]
    for event in selected:
        event_id = str(event.get("id") or "").strip()
        if not event_id:
            continue
        keys, meta = discover_event_market_keys(
            sport_key, event_id, regions=regions, bookmakers=bookmakers, max_markets=max_markets,
        )
        if meta.get("ok"):
            discovered[event_id] = keys
        else:
            errors.append({"event_id": event_id, "error": meta.get("error"), "status": meta.get("status")})
    return {
        "ok": bool(discovered),
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
    max_markets_per_event: int = 120,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    events, events_meta = discover_events(sport_key)
    if not events_meta.get("ok"):
        return [], {"ok": False, "stage": "events", "events": events_meta}
    selected = events[:max(0, int(max_events))]
    normalized: List[Dict[str, Any]] = []
    details: List[Dict[str, Any]] = []
    candidates = candidate_markets_for_sport(sport_key)[:max(1, int(max_markets_per_event))]
    for event in selected:
        event_id = str(event["id"])
        accepted, rows, evidence = _probe_market_batch(
            sport_key, event_id, candidates, regions=regions, bookmakers=bookmakers,
        )
        normalized.extend(rows)
        details.append({
            "event_id": event_id,
            "ok": bool(accepted),
            "n_candidates": len(candidates),
            "n_discovered": len(accepted),
            "markets": accepted,
            "probe_requests": len(evidence),
        })
    good = sum(1 for x in details if x.get("ok"))
    return normalized, {
        "ok": good > 0,
        "stage": "complete",
        "discovery": "documented_catalog_runtime_probe",
        "n_events_discovered": len(events),
        "n_events_requested": len(selected),
        "n_events_succeeded": good,
        "n_normalized_markets": len(normalized),
        "event_details": details[:100],
        "partial": len(selected) < len(events) or len(candidate_markets_for_sport(sport_key)) > len(candidates),
    }
