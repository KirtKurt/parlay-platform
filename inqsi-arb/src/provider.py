"""Permitted market-data adapter for Inqsi Arb.

The adapter intentionally discovers sports and bookmakers from the provider
instead of maintaining a short hard-coded book list. Arbitrary provider market
keys are accepted; unsupported markets fail closed.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

BASE = "https://api.the-odds-api.com/v4"
FEATURED = {"h2h", "spreads", "totals", "outrights"}

MARKET_FAMILIES = {
    "winner": ["h2h", "h2h_3_way", "draw_no_bet", "outrights"],
    "spread_handicap": ["spreads", "alternate_spreads", "alternate_team_spreads"],
    "totals": ["totals", "alternate_totals", "team_totals", "alternate_team_totals"],
    "periods": ["h2h_1st_*", "spreads_1st_*", "totals_1st_*"],
    "player_props": ["player_*"],
    "team_props": ["team_*"],
    "game_props": ["game_*", "first_*", "race_to_*", "odd_even"],
    "yes_no": ["*_yes_no"],
    "futures": ["outrights", "*_winner", "*_championship", "*_award"],
    "exchange": ["h2h_lay"],
}


def _get(url: str, params: Mapping[str, Any], timeout: int = 15) -> Tuple[Any, Dict[str, Any]]:
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
    request = urllib.request.Request(url + ("?" + query if query else ""), headers={"accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            headers = {str(k).lower(): str(v) for k, v in response.headers.items()}
            body = json.loads(response.read().decode("utf-8"))
            return body, {
                "ok": True,
                "status": int(getattr(response, "status", response.getcode())),
                "requests_remaining": headers.get("x-requests-remaining"),
                "requests_used": headers.get("x-requests-used"),
                "requests_last": headers.get("x-requests-last"),
            }
    except urllib.error.HTTPError as exc:
        return None, {"ok": False, "status": exc.code, "error": f"PROVIDER_HTTP_{exc.code}"}
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None, {"ok": False, "error": "PROVIDER_UNAVAILABLE"}


def api_key() -> str:
    return (os.environ.get("ODDS_API_KEY") or os.environ.get("THE_ODDS_API_KEY") or "").strip()


def list_sports(*, all_sports: bool = False) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    key = api_key()
    if not key:
        return [], {"ok": False, "error": "ODDS_API_KEY_MISSING"}
    payload, meta = _get(f"{BASE}/sports/", {"apiKey": key, "all": "true" if all_sports else "false"})
    if not meta.get("ok") or not isinstance(payload, list):
        return [], meta
    sports = []
    for s in payload:
        if not isinstance(s, dict) or not s.get("key"):
            continue
        sports.append({
            "key": str(s.get("key")),
            "group": s.get("group"),
            "title": s.get("title"),
            "description": s.get("description"),
            "active": bool(s.get("active", True)),
            "has_outrights": bool(s.get("has_outrights", False)),
        })
    return sports, meta


def _canon_number(value: Any) -> Optional[float]:
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return None


def _bucket_identity(market_key: str, outcome: Mapping[str, Any]) -> Tuple[str, str]:
    name = str(outcome.get("name") or "").strip()
    desc = str(outcome.get("description") or "").strip()
    point = _canon_number(outcome.get("point"))
    line = abs(point) if point is not None and ("spread" in market_key or "handicap" in market_key) else point
    identity = f"{market_key}|{desc}|{line if line is not None else ''}"
    outcome_id = f"{desc} :: {name}" if desc else name
    if point is not None and ("spread" in market_key or "handicap" in market_key):
        outcome_id += f" {point:+g}"
    return identity, outcome_id


def normalize_games(games: Sequence[Mapping[str, Any]], *, sport_key: str, provider: str = "the-odds-api") -> List[Dict[str, Any]]:
    buckets: Dict[str, Dict[str, Any]] = {}
    outcome_sets_by_book: Dict[str, Dict[str, set[str]]] = {}
    for game in games or []:
        event_id = str(game.get("id") or "").strip()
        if not event_id:
            continue
        home = str(game.get("home_team") or "").strip()
        away = str(game.get("away_team") or "").strip()
        label = f"{away} @ {home}" if home and away else event_id
        for bookmaker in game.get("bookmakers") or []:
            book = str(bookmaker.get("key") or bookmaker.get("title") or "").strip().lower()
            if not book:
                continue
            book_update = bookmaker.get("last_update")
            for market in bookmaker.get("markets") or []:
                market_key = str(market.get("key") or "").strip()
                if not market_key:
                    continue
                market_update = market.get("last_update") or book_update
                for outcome in market.get("outcomes") or []:
                    price = outcome.get("price")
                    try:
                        float(price)
                    except (TypeError, ValueError):
                        continue
                    sub_id, outcome_id = _bucket_identity(market_key, outcome)
                    market_id = f"{event_id}|{sub_id}"
                    bucket = buckets.setdefault(market_id, {
                        "id": market_id,
                        "event_id": event_id,
                        "event": label,
                        "sport": sport_key,
                        "market": market_key,
                        "market_identity": sub_id,
                        "commence_time": game.get("commence_time"),
                        "rules_status": "provider_identity_only",
                        "quotes": [],
                        "context": {"sport_title": game.get("sport_title"), "home_team": home, "away_team": away},
                    })
                    bucket["quotes"].append({
                        "outcome": outcome_id,
                        "book": book,
                        "american": price,
                        "provider": provider,
                        "last_update": market_update,
                        "link": outcome.get("link") or market.get("link") or bookmaker.get("link"),
                        "limit": outcome.get("bet_limit"),
                    })
                    outcome_sets_by_book.setdefault(market_id, {}).setdefault(book, set()).add(outcome_id)

    out: List[Dict[str, Any]] = []
    for market_id, bucket in buckets.items():
        sets = list(outcome_sets_by_book.get(market_id, {}).values())
        union = set().union(*sets) if sets else set()
        complete_reference = any(s == union for s in sets) and len(union) >= 2
        bucket["expected_outcomes"] = sorted(union)
        if not complete_reference:
            bucket["rules_status"] = "unknown"
            bucket["context"]["coverage_reason"] = "no_book_has_complete_outcome_universe"
        out.append(bucket)
    return out


def fetch_featured_odds(sport_key: str, *, markets: Iterable[str], regions: str, bookmakers: Optional[str] = None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    key = api_key()
    if not key:
        return [], {"ok": False, "error": "ODDS_API_KEY_MISSING"}
    requested = [m.strip() for m in markets if m.strip()]
    payload, meta = _get(
        f"{BASE}/sports/{urllib.parse.quote(sport_key, safe='')}/odds/",
        {
            "apiKey": key,
            "regions": regions,
            "markets": ",".join(requested),
            "bookmakers": bookmakers,
            "oddsFormat": "american",
            "dateFormat": "iso",
            "includeLinks": "true",
            "includeBetLimits": "true",
        },
    )
    if not meta.get("ok") or not isinstance(payload, list):
        return [], meta
    return normalize_games(payload, sport_key=sport_key), {**meta, "n_games": len(payload), "markets": requested}


def fetch_event_markets(sport_key: str, market_keys: Iterable[str], *, regions: str, bookmakers: Optional[str] = None, max_events: int = 100) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    key = api_key()
    if not key:
        return [], {"ok": False, "error": "ODDS_API_KEY_MISSING"}
    events, events_meta = _get(
        f"{BASE}/sports/{urllib.parse.quote(sport_key, safe='')}/events/",
        {"apiKey": key, "dateFormat": "iso"},
    )
    if not events_meta.get("ok") or not isinstance(events, list):
        return [], {"ok": False, "events": events_meta}
    selected = events[:max(0, int(max_events))]
    results: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    metas: List[Dict[str, Any]] = []
    market_csv = ",".join(m.strip() for m in market_keys if m.strip())
    for event in selected:
        event_id = str(event.get("id") or "")
        if not event_id:
            continue
        payload, meta = _get(
            f"{BASE}/sports/{urllib.parse.quote(sport_key, safe='')}/events/{urllib.parse.quote(event_id, safe='')}/odds",
            {
                "apiKey": key,
                "regions": regions,
                "markets": market_csv,
                "bookmakers": bookmakers,
                "oddsFormat": "american",
                "dateFormat": "iso",
                "includeLinks": "true",
                "includeBetLimits": "true",
            },
        )
        metas.append(meta)
        if meta.get("ok") and isinstance(payload, dict):
            results.extend(normalize_games([payload], sport_key=sport_key))
        else:
            errors.append({"event_id": event_id, "error": meta.get("error"), "status": meta.get("status")})
    return results, {
        "ok": not errors or bool(results),
        "n_events_discovered": len(events),
        "n_events_requested": len(selected),
        "n_markets": len(results),
        "errors": errors[:20],
        "request_meta": metas[-1] if metas else events_meta,
    }


def scan_sport_payload(sport_key: str, *, bankroll: float, markets: Sequence[str], regions: Optional[str] = None, bookmakers: Optional[str] = None, max_events: Optional[int] = None) -> Dict[str, Any]:
    regions = regions or os.environ.get("ARB_REGIONS", "us,us2,uk,eu,au")
    max_events = int(max_events if max_events is not None else os.environ.get("ARB_MAX_EVENT_MARKET_EVENTS", "40"))
    featured = [m for m in markets if m in FEATURED]
    extended = [m for m in markets if m not in FEATURED]
    events: List[Dict[str, Any]] = []
    status: Dict[str, Any] = {"sport": sport_key, "regions": regions, "bookmakers_filter": bookmakers, "featured": None, "extended": None}
    if featured:
        rows, meta = fetch_featured_odds(sport_key, markets=featured, regions=regions, bookmakers=bookmakers)
        events.extend(rows)
        status["featured"] = meta
    if extended:
        rows, meta = fetch_event_markets(sport_key, extended, regions=regions, bookmakers=bookmakers, max_events=max_events)
        events.extend(rows)
        status["extended"] = meta
    status["ok"] = any((section or {}).get("ok") for section in (status["featured"], status["extended"]))
    status["ts"] = datetime.now(timezone.utc).isoformat()
    status["n_normalized_markets"] = len(events)
    return {"bankroll": bankroll, "events": events, "status": status}
