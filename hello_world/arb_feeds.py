"""Normalize The Odds API + Big Balls Data into scan events.

Odds API is the book-price source. BBD is match context (starters / injuries
when present) and is only treated as a quote book if it actually ships prices.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

try:
    from bigballsdata_client import BBSClientError, BigBallsDataClient
except ImportError:  # pragma: no cover
    try:
        from hello_world.bigballsdata_client import BBSClientError, BigBallsDataClient
    except ImportError:
        BBSClientError = RuntimeError  # type: ignore
        BigBallsDataClient = None  # type: ignore


SPORT_KEYS = {
    "mlb": "baseball_mlb",
    "nba": "basketball_nba",
    "nfl": "americanfootball_nfl",
    "ncaam": "basketball_ncaab",
    "nhl": "icehockey_nhl",
    "soccer": "soccer_usa_mls",
}

ODDS_V4 = "https://api.the-odds-api.com/v4/sports/{sport}/odds/"


def slate_date_et() -> str:
    return datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")


def _norm_name(value: Optional[str]) -> str:
    return "".join(ch for ch in (value or "").lower() if ch.isalnum())


def american_ok(value: Any) -> bool:
    try:
        return float(value) != 0
    except (TypeError, ValueError):
        return False


def fetch_odds_api(
    api_key: str,
    *,
    sport: str = "mlb",
    markets: str = "h2h,spreads,totals",
    regions: str = "us",
    timeout: int = 20,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    meta: Dict[str, Any] = {
        "ok": False,
        "source": "the-odds-api",
        "sport": sport,
        "status": None,
        "error": None,
        "credits": {},
        "n_games": 0,
    }
    if not api_key:
        meta["error"] = "ODDS_API_KEY_MISSING"
        return [], meta
    sport_key = SPORT_KEYS.get(sport, sport)
    params = {
        "apiKey": api_key,
        "regions": regions,
        "markets": markets,
        "oddsFormat": "american",
        "dateFormat": "iso",
    }
    url = ODDS_V4.format(sport=sport_key) + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            meta["status"] = int(getattr(resp, "status", resp.getcode()))
            headers = {str(k).lower(): str(v) for k, v in resp.headers.items()}
            meta["credits"] = {
                "remaining": headers.get("x-requests-remaining"),
                "used": headers.get("x-requests-used"),
            }
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        meta["status"] = exc.code
        meta["error"] = f"ODDS_API_HTTP_{exc.code}"
        return [], meta
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        meta["error"] = "ODDS_API_NETWORK"
        return [], meta
    if not isinstance(payload, list):
        meta["error"] = "ODDS_API_SHAPE"
        return [], meta
    events = odds_games_to_events(payload)
    meta["ok"] = True
    meta["n_games"] = len(payload)
    meta["n_events"] = len(events)
    return events, meta


def odds_games_to_events(games: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for game in games or []:
        home = game.get("home_team") or ""
        away = game.get("away_team") or ""
        gid = game.get("id") or _norm_name(f"{away}{home}")
        commence = game.get("commence_time")
        buckets: Dict[str, Dict[str, Any]] = {}
        for bookmaker in game.get("bookmakers") or []:
            book = (bookmaker.get("key") or bookmaker.get("title") or "book").lower()
            for market in bookmaker.get("markets") or []:
                mkey = market.get("key") or "h2h"
                for outcome in market.get("outcomes") or []:
                    price = outcome.get("price")
                    if not american_ok(price):
                        continue
                    name = str(outcome.get("name") or "").strip()
                    point = outcome.get("point")
                    if mkey in {"spreads", "totals"} and point is not None:
                        market_id = f"{gid}:{mkey}:{point}"
                        label = f"{mkey} {point}"
                        if mkey == "totals":
                            side = name
                        else:
                            side = f"{name} {point}"
                    else:
                        market_id = f"{gid}:{mkey}"
                        label = mkey
                        side = name
                    bucket = buckets.setdefault(
                        market_id,
                        {
                            "id": market_id,
                            "event": f"{away} @ {home}",
                            "market": label,
                            "commence_time": commence,
                            "home_team": home,
                            "away_team": away,
                            "source": "the-odds-api",
                            "quotes": [],
                        },
                    )
                    bucket["quotes"].append(
                        {"outcome": side, "book": book, "american": price}
                    )
        out.extend(buckets.values())
    return out


def fetch_bbd_context(
    *,
    game_date: Optional[str] = None,
    timeout: int = 4,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Pull today's MLB matches from BBD. Soft-fail; never leak the key."""
    meta: Dict[str, Any] = {
        "ok": False,
        "source": "bigballsdata",
        "error": None,
        "n_matches": 0,
    }
    if BigBallsDataClient is None:
        meta["error"] = "BBD_CLIENT_UNAVAILABLE"
        return [], meta
    date = game_date or slate_date_et()
    meta["date"] = date
    try:
        client = BigBallsDataClient(timeout_seconds=timeout)
        payload = client.list_mlb_matches(date)
    except BBSClientError as exc:
        meta["error"] = str(exc)[:80]
        return [], meta
    except Exception:
        meta["error"] = "BBD_UNAVAILABLE"
        return [], meta
    matches = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(matches, list):
        meta["error"] = "BBD_SHAPE"
        return [], meta
    rows = [normalize_bbd_match(m) for m in matches]
    rows = [r for r in rows if r]
    meta["ok"] = True
    meta["n_matches"] = len(rows)
    transport = payload.get("_transport") if isinstance(payload, dict) else None
    if isinstance(transport, dict):
        meta["rateRemaining"] = transport.get("rateRemaining")
    return rows, meta


def normalize_bbd_match(match: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(match, dict):
        return None
    home = (
        match.get("home_team")
        or match.get("homeTeam")
        or (match.get("home") or {}).get("name")
        or match.get("home_name")
    )
    away = (
        match.get("away_team")
        or match.get("awayTeam")
        or (match.get("away") or {}).get("name")
        or match.get("away_name")
    )
    if not home or not away:
        return None
    context = {
        "bbd_match_id": match.get("id") or match.get("match_id"),
        "status": match.get("status"),
        "home_pitcher": _first(
            match,
            "home_pitcher",
            "homePitcher",
            "probable_home_pitcher",
        ),
        "away_pitcher": _first(
            match,
            "away_pitcher",
            "awayPitcher",
            "probable_away_pitcher",
        ),
    }
    quotes = []
    ml = match.get("ml") or match.get("moneyline") or {}
    if isinstance(ml, dict):
        if american_ok(ml.get("home")):
            quotes.append({"outcome": str(home), "book": "bbd", "american": ml["home"]})
        if american_ok(ml.get("away")):
            quotes.append({"outcome": str(away), "book": "bbd", "american": ml["away"]})
    return {
        "id": f"bbd-{context['bbd_match_id'] or _norm_name(str(away)+str(home))}:h2h",
        "event": f"{away} @ {home}",
        "market": "h2h",
        "home_team": home,
        "away_team": away,
        "source": "bbd",
        "quotes": quotes,
        "context": {k: v for k, v in context.items() if v is not None},
    }


def _first(match: Dict[str, Any], *keys: str) -> Optional[Any]:
    for key in keys:
        value = match.get(key)
        if value:
            return value
    return None


def merge_events(
    odds_events: List[Dict[str, Any]],
    bbd_rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Attach BBD context to Odds API markets; union quotes when BBD has prices."""
    index: Dict[str, Dict[str, Any]] = {}
    for row in bbd_rows:
        index[_norm_name(row.get("event"))] = row
    merged: List[Dict[str, Any]] = []
    used = set()
    for ev in odds_events:
        key = _norm_name(ev.get("event"))
        extra = index.get(key)
        item = dict(ev)
        item["quotes"] = list(ev.get("quotes") or [])
        item["sources"] = [ev.get("source") or "the-odds-api"]
        if extra:
            used.add(key)
            ctx = dict(extra.get("context") or {})
            item["context"] = {**(item.get("context") or {}), **ctx}
            item["quotes"].extend(extra.get("quotes") or [])
            item["sources"].append("bbd")
        merged.append(item)
    for key, extra in index.items():
        if key in used:
            continue
        if extra.get("quotes"):
            item = dict(extra)
            item["sources"] = ["bbd"]
            merged.append(item)
    return merged


def live_scan_payload(
    *,
    sport: str = "mlb",
    bankroll: float = 1000.0,
    markets: str = "h2h,spreads,totals",
) -> Dict[str, Any]:
    odds_key = (
        os.environ.get("ODDS_API_KEY")
        or os.environ.get("THE_ODDS_API_KEY")
        or ""
    ).strip()
    odds_events, odds_meta = fetch_odds_api(odds_key, sport=sport, markets=markets)
    bbd_rows, bbd_meta = ([], {"ok": False, "source": "bigballsdata", "error": "BBD_SKIPPED_NON_MLB"})
    if sport == "mlb":
        bbd_rows, bbd_meta = fetch_bbd_context()
    events = merge_events(odds_events, bbd_rows)
    return {
        "bankroll": float(bankroll or 1000.0),
        "events": events,
        "status": {
            "ts": datetime.now(timezone.utc).isoformat(),
            "sport": sport,
            "slate_date_et": slate_date_et(),
            "odds_api": odds_meta,
            "bbd": bbd_meta,
            "n_unified": len(events),
            "keys_present": {
                "odds_api": bool(odds_key),
                "bbd": bool(os.environ.get("BBS_API_KEY") or os.environ.get("BBD_API_KEY") or os.environ.get("BBS_API_SECRET_ARN")),
            },
            "places_bets": False,
        },
    }
