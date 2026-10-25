"""Automatic event-market enumeration for Inqsi ARB.

The provider event-markets endpoint is the primary runtime inventory because it
returns recently observed market keys per bookmaker for a specific event. Inqsi
then requests only those observed keys from the event-odds endpoint. The static
sport catalog remains a fail-closed fallback when market inventory is
unavailable; unsupported fallback keys are isolated by recursive bisection.
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


def _event_markets_url(sport_key: str, event_id: str) -> str:
    return (
        f"{BASE}/sports/{urllib.parse.quote(str(sport_key), safe='')}"
        f"/events/{urllib.parse.quote(str(event_id), safe='')}/markets"
    )


def _discover_event_markets_direct(
    sport_key: str,
    event_id: str,
    *,
    regions: str,
    bookmakers: Optional[str] = None,
) -> Tuple[List[str], Dict[str, Any]]:
    payload, meta = _get(_event_markets_url(sport_key, event_id), {
        "apiKey": api_key(),
        "regions": regions,
        "bookmakers": bookmakers,
        "dateFormat": "iso",
    })
    if not meta.get("ok") or not isinstance(payload, dict):
        return [], {**meta, "discovery": "event_markets_endpoint"}
    keys: List[str] = []
    for book in payload.get("bookmakers") or []:
        if not isinstance(book, dict):
            continue
        for market in book.get("markets") or []:
            if not isinstance(market, dict):
                continue
            key = str(market.get("key") or "").strip()
            if key and key not in keys:
                keys.append(key)
    return keys, {
        **meta,
        "ok": True,
        "discovery": "event_markets_endpoint",
        "n_market_keys": len(keys),
    }


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
    # Only malformed/unsupported market combinations can benefit from bisection.
    # Authentication, throttling and provider outages affect every sub-batch;
    # retrying them recursively amplifies requests without restoring coverage.
    status = meta.get("status")
    # Only the provider's invalid-request/unsupported-market responses justify
    # splitting market keys.  All other statuses (including unknown failures,
    # redirects and malformed success bodies) must fail closed without fan-out.
    if status not in (400, 422):
        return [], [], evidence
    if len(markets) == 1:
        return [], [], evidence
    midpoint = len(markets) // 2
    left = _probe_market_batch(sport_key, event_id, markets[:midpoint], regions=regions, bookmakers=bookmakers, depth=depth + 1)
    right = _probe_market_batch(sport_key, event_id, markets[midpoint:], regions=regions, bookmakers=bookmakers, depth=depth + 1)
    accepted = list(dict.fromkeys(left[0] + right[0]))
    return accepted, left[1] + right[1], evidence + left[2] + right[2]



def _primary_probe_failure(
    evidence: List[Dict[str, Any]], *, terminal_only: bool = False
) -> Dict[str, Any]:
    """Prefer a terminal provider error over an earlier unsupported-market 422.

    A batch can fail with 422, then reveal a 401/429/outage in a child probe.
    Reporting only the initial 422 would conceal the actionable failure.
    """
    for row in evidence:
        status = row.get("status")
        if (
            status in (401, 403, 404, 408, 410, 429)
            or (isinstance(status, int) and status >= 500)
            or row.get("error") == "PROVIDER_UNAVAILABLE"
        ):
            return row
    return {} if terminal_only else (evidence[0] if evidence else {})


def discover_event_market_keys(
    sport_key: str,
    event_id: str,
    *,
    regions: str = "us,us2,us_dfs,us_ex,uk,eu,ca,fr,se,fi,au",
    bookmakers: Optional[str] = None,
    max_markets: int = 250,
) -> Tuple[List[str], Dict[str, Any]]:
    if not api_key():
        return [], {"ok": False, "error": "ODDS_API_KEY_MISSING"}

    observed, observed_meta = _discover_event_markets_direct(
        sport_key, event_id, regions=regions, bookmakers=bookmakers,
    )
    limit = max(1, int(max_markets))
    if observed_meta.get("ok"):
        selected = observed[:limit]
        return selected, {
            **observed_meta,
            "n_candidates": len(observed),
            "n_market_keys": len(selected),
            "partial": len(observed) > len(selected),
            "fallback_used": False,
        }

    candidates = candidate_markets_for_sport(sport_key)[:limit]
    accepted, _rows, evidence = _probe_market_batch(
        sport_key, event_id, candidates, regions=regions, bookmakers=bookmakers,
    )
    # A failed fallback must preserve the actionable provider error.  Without
    # these fields, callers see a failed discovery with error/status = None.
    probe_failure = _primary_probe_failure(evidence, terminal_only=bool(accepted))
    return accepted, {
        "ok": bool(accepted),
        "error": probe_failure.get("error") or (observed_meta.get("error") if not accepted else None),
        "status": probe_failure.get("status") if probe_failure else (observed_meta.get("status") if not accepted else None),
        "discovery": "documented_catalog_runtime_probe_fallback",
        "n_candidates": len(candidates),
        "n_market_keys": len(accepted),
        "partial": True,  # A static fallback cannot prove exhaustive provider-visible coverage.
        "probe_requests": len(evidence),
        "evidence": evidence[:50],
        "fallback_used": True,
        "event_markets_error": observed_meta.get("error"),
        "event_markets_status": observed_meta.get("status"),
    }


def discover_sport_event_markets(
    sport_key: str,
    events: List[Dict[str, Any]],
    *,
    regions: str = "us,us2,us_dfs,us_ex,uk,eu,ca,fr,se,fi,au",
    bookmakers: Optional[str] = None,
    max_events: int = 40,
    max_markets: int = 120,
) -> Dict[str, Any]:
    discovered: Dict[str, List[str]] = {}
    errors: List[Dict[str, Any]] = []
    fallback_events: List[str] = []
    selected = (events or [])[:max(0, int(max_events))]
    # This summary must not imply exhaustive provider coverage when an event
    # was capped, failed, or relied on a static catalog instead of live inventory.
    partial = len(selected) < len(events or [])
    for event in selected:
        event_id = str(event.get("id") or "").strip()
        if not event_id:
            partial = True
            continue
        keys, meta = discover_event_market_keys(
            sport_key, event_id, regions=regions, bookmakers=bookmakers, max_markets=max_markets,
        )
        if meta.get("fallback_used"):
            fallback_events.append(event_id)
        if not meta.get("ok") or meta.get("partial") or meta.get("fallback_used"):
            partial = True
        if meta.get("ok"):
            discovered[event_id] = keys
        else:
            errors.append({"event_id": event_id, "error": meta.get("error"), "status": meta.get("status")})
    return {
        "ok": bool(discovered),
        "sport": sport_key,
        "events": discovered,
        "n_events": len(discovered),
        "n_events_requested": len(selected),
        "n_events_available": len(events or []),
        "partial": partial,
        "fallback_events": fallback_events[:20],
        "unique_markets": sorted({k for values in discovered.values() for k in values}),
        "errors": errors[:20],
    }


def fetch_all_discovered_markets(
    sport_key: str,
    *,
    regions: str,
    bookmakers: Optional[str] = None,
    max_events: int = 40,
    max_markets_per_event: int = 250,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    events, events_meta = discover_events(sport_key)
    if not events_meta.get("ok"):
        return [], {"ok": False, "stage": "events", "events": events_meta}
    selected = events[:max(0, int(max_events))]
    normalized: List[Dict[str, Any]] = []
    details: List[Dict[str, Any]] = []
    limit = max(1, int(max_markets_per_event))
    for event in selected:
        event_id = str(event["id"])
        observed, discovery_meta = _discover_event_markets_direct(
            sport_key, event_id, regions=regions, bookmakers=bookmakers,
        )
        fallback_used = not discovery_meta.get("ok")
        candidates = (
            observed[:limit]
            if not fallback_used
            else candidate_markets_for_sport(sport_key)[:limit]
        )
        accepted, rows, evidence = _probe_market_batch(
            sport_key, event_id, candidates, regions=regions, bookmakers=bookmakers,
        ) if candidates else ([], [], [])
        normalized.extend(rows)
        probe_failure = _primary_probe_failure(evidence, terminal_only=bool(accepted))
        details.append({
            "event_id": event_id,
            "ok": bool(accepted) or (not fallback_used and not candidates),
            "error": probe_failure.get("error") or (discovery_meta.get("error") if not accepted else None),
            "status": probe_failure.get("status") if probe_failure else (discovery_meta.get("status") if not accepted else None),
            "event_markets_error": discovery_meta.get("error"),
            "event_markets_status": discovery_meta.get("status"),
            "n_candidates": len(candidates),
            "n_observed": len(observed),
            "n_discovered": len(accepted),
            "markets": accepted,
            "probe_requests": len(evidence),
            "discovery": discovery_meta.get("discovery"),
            "fallback_used": fallback_used,
            # An inventory response alone cannot prove full price coverage when
            # one or more requested market keys failed to return usable odds.
            "partial": fallback_used or len(observed) > len(candidates) or bool(set(candidates) - set(accepted)),
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
        "partial": len(selected) < len(events) or any(bool(x.get("partial")) for x in details),
    }
