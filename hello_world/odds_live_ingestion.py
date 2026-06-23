import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    import inqsi_pull_history
except Exception:
    inqsi_pull_history = None

# Keep CFB canonical in live ingestion even if older pull-history aliases drift.
if inqsi_pull_history is not None and hasattr(inqsi_pull_history, "ALIASES"):
    try:
        inqsi_pull_history.ALIASES.update({
            "cfb": "cfb",
            "ncaaf": "cfb",
            "college_football": "cfb",
            "college_football_men": "cfb",
        })
    except Exception:
        pass

ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
ODDS_REGIONS = os.environ.get("ODDS_REGIONS", "us")
ODDS_MARKETS = os.environ.get("ODDS_MARKETS", "h2h,spreads,totals")
ODDS_FORMAT = os.environ.get("ODDS_FORMAT", "american")

SPORT_PROVIDER_MAP = {
    "nfl": ["americanfootball_nfl"],
    "cfb": ["americanfootball_ncaaf"],
    "college_football_men": ["americanfootball_ncaaf"],
    "mlb": ["baseball_mlb"],
    "college_baseball_men": ["baseball_ncaa"],
    "nba": ["basketball_nba"],
    "wnba": ["basketball_wnba"],
    "ncaam": ["basketball_ncaab"],
    "ncaaw": ["basketball_ncaab"],
    "nhl": ["icehockey_nhl"],
    "tennis": ["tennis_atp_singles", "tennis_wta_singles"],
    "soccer": ["soccer_usa_mls", "soccer_epl", "soccer_uefa_champs_league"],
}

DEFAULT_PULL_SEQUENCE = ["nfl", "cfb", "mlb", "college_baseball_men", "nba", "wnba", "ncaam", "nhl", "tennis", "soccer"]

# Active-slate windows prevent future-season betting markets from polluting today's parlay board.
# Values are days from pull time, with a small back buffer for just-started/live markets.
DEFAULT_SLATE_WINDOW_DAYS = {
    "mlb": 2,
    "college_baseball_men": 2,
    "nba": 2,
    "wnba": 2,
    "ncaam": 2,
    "ncaaw": 2,
    "nhl": 2,
    "nfl": 7,
    "cfb": 7,
    "college_football_men": 7,
    "soccer": 14,
    "tennis": 7,
}
ACTIVE_WINDOW_BACK_BUFFER_HOURS = int(os.environ.get("INQSI_ACTIVE_WINDOW_BACK_BUFFER_HOURS", "6"))

CANONICAL_ALIASES = {
    "ncaaf": "cfb",
    "college_football": "cfb",
    "college_football_men": "cfb",
    "college_fb": "cfb",
    "ncaa_football": "cfb",
    "college_basketball_men": "ncaam",
    "ncaab": "ncaam",
    "college_basketball_women": "ncaaw",
    "ncaawb": "ncaaw",
    "college_baseball": "college_baseball_men",
    "ncaa_baseball": "college_baseball_men",
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def out(status: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json",
            "access-control-allow-origin": "*",
            "access-control-allow-methods": "GET,POST,OPTIONS",
            "access-control-allow-headers": "content-type,authorization,x-inqsi-admin-token,x-inqsi-member-id,x-inqsi-session-id",
        },
        "body": json.dumps(body),
    }


def params(event: Dict[str, Any]) -> Dict[str, Any]:
    data = {}
    q = event.get("queryStringParameters") or {}
    data.update(q)
    try:
        body = json.loads(event.get("body") or "{}")
        if isinstance(body, dict):
            data.update(body)
    except Exception:
        pass
    return data


def http_get_json(url: str, timeout: int = 25) -> Any:
    req = urllib.request.Request(url, headers={"accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def odds_url(provider_sport_key: str) -> str:
    if not ODDS_API_KEY:
        raise RuntimeError("ODDS_API_KEY missing")
    query = {
        "apiKey": ODDS_API_KEY,
        "regions": ODDS_REGIONS,
        "markets": ODDS_MARKETS,
        "oddsFormat": ODDS_FORMAT,
        "dateFormat": "iso",
    }
    return "https://api.the-odds-api.com/v4/sports/" + provider_sport_key + "/odds/?" + urllib.parse.urlencode(query)


def sports_url() -> str:
    if not ODDS_API_KEY:
        raise RuntimeError("ODDS_API_KEY missing")
    return "https://api.the-odds-api.com/v4/sports/?" + urllib.parse.urlencode({"apiKey": ODDS_API_KEY})


def safe_error(exc: Exception) -> Dict[str, Any]:
    payload = {
        "errorType": type(exc).__name__,
        "message": str(exc),
        "oddsApiKeyPresent": bool(ODDS_API_KEY),
        "secretExposed": False,
    }
    if isinstance(exc, urllib.error.HTTPError):
        body = ""
        try:
            body = exc.read().decode("utf-8")[:300]
        except Exception:
            pass
        payload.update({"httpStatus": exc.code, "reason": exc.reason, "upstreamBodySample": body})
    return payload


def sport_key(value: str) -> str:
    raw = (value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if raw in CANONICAL_ALIASES:
        return CANONICAL_ALIASES[raw]
    if raw == "cfb":
        return "cfb"
    if inqsi_pull_history is not None:
        try:
            return inqsi_pull_history.sport_key(raw)
        except Exception:
            pass
    return raw


def market(book: Dict[str, Any], key: str) -> Optional[Dict[str, Any]]:
    for m in book.get("markets", []) or []:
        if m.get("key") == key:
            return m
    return None


def h2h(book: Dict[str, Any], home: str, away: str) -> Optional[Dict[str, Any]]:
    m = market(book, "h2h")
    if not m:
        return None
    home_price = away_price = None
    for o in m.get("outcomes", []) or []:
        if o.get("name") == home:
            home_price = o.get("price")
        elif o.get("name") == away:
            away_price = o.get("price")
    if home_price is None or away_price is None:
        return None
    return {"home": int(home_price), "away": int(away_price)}


def spread(book: Dict[str, Any], home: str, away: str) -> Optional[Dict[str, Any]]:
    m = market(book, "spreads")
    if not m:
        return None
    result: Dict[str, Any] = {}
    for o in m.get("outcomes", []) or []:
        if o.get("name") == home:
            result["home_point"] = o.get("point")
            result["home_price"] = o.get("price")
        elif o.get("name") == away:
            result["away_point"] = o.get("point")
            result["away_price"] = o.get("price")
    return result if len(result) == 4 else None


def total(book: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    m = market(book, "totals")
    if not m:
        return None
    result: Dict[str, Any] = {}
    for o in m.get("outcomes", []) or []:
        name = (o.get("name") or "").lower()
        if name == "over":
            result["over_point"] = o.get("point")
            result["over_price"] = o.get("price")
        elif name == "under":
            result["under_point"] = o.get("point")
            result["under_price"] = o.get("price")
    return result if len(result) == 4 else None


def parse_commence_time(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def slate_window_days(app_sport: str) -> int:
    sport = sport_key(app_sport)
    env_key = "INQSI_SLATE_WINDOW_DAYS_" + sport.upper()
    if os.environ.get(env_key):
        try:
            return max(1, int(os.environ[env_key]))
        except Exception:
            pass
    if os.environ.get("INQSI_SLATE_WINDOW_DAYS_ALL"):
        try:
            return max(1, int(os.environ["INQSI_SLATE_WINDOW_DAYS_ALL"]))
        except Exception:
            pass
    return int(DEFAULT_SLATE_WINDOW_DAYS.get(sport, 2))


def active_window(app_sport: str) -> Tuple[datetime, datetime, int]:
    days = slate_window_days(app_sport)
    current = datetime.now(timezone.utc)
    start = current - timedelta(hours=ACTIVE_WINDOW_BACK_BUFFER_HOURS)
    end = current + timedelta(days=days)
    return start, end, days


def is_in_active_window(raw: Dict[str, Any], app_sport: str) -> bool:
    start, end, _ = active_window(app_sport)
    commence = parse_commence_time(raw.get("commence_time"))
    if commence is None:
        return False
    return start <= commence <= end


def filter_active_slate(raw_games: Any, app_sport: str) -> List[Dict[str, Any]]:
    if not isinstance(raw_games, list):
        return []
    return [g for g in raw_games if isinstance(g, dict) and is_in_active_window(g, app_sport)]


def convert_game(raw: Dict[str, Any], app_sport: str, provider_sport_key: str) -> Optional[Dict[str, Any]]:
    home = raw.get("home_team")
    away = raw.get("away_team")
    if not home or not away:
        return None
    books: Dict[str, Any] = {}
    for bookmaker in raw.get("bookmakers", []) or []:
        book_key = (bookmaker.get("key") or "").lower().strip()
        if not book_key:
            continue
        payload: Dict[str, Any] = {}
        ml = h2h(bookmaker, home, away)
        sp = spread(bookmaker, home, away)
        to = total(bookmaker)
        if ml:
            payload["moneyline"] = ml
            payload["ml"] = ml
        if sp:
            payload["spread"] = sp
        if to:
            payload["total"] = to
        if payload:
            books[book_key] = payload
    if not books:
        return None
    game_id = raw.get("id") or f"{provider_sport_key}|{away}|{home}|{raw.get('commence_time')}"
    return {
        "game_id": str(game_id),
        "id": str(game_id),
        "game_key": f"{app_sport}|{provider_sport_key}|{away.lower()}|{home.lower()}|{raw.get('commence_time')}",
        "home_team": home,
        "away_team": away,
        "commence_time": raw.get("commence_time"),
        "provider_sport_key": provider_sport_key,
        "books": books,
    }


def provider_keys_for(app_sport: str) -> List[str]:
    app_sport = sport_key(app_sport)
    return SPORT_PROVIDER_MAP.get(app_sport, [app_sport])


def pull_one(app_sport: str, provider_sport_key: str) -> Dict[str, Any]:
    app_sport = sport_key(app_sport)
    raw = http_get_json(odds_url(provider_sport_key))
    raw_count = len(raw) if isinstance(raw, list) else 0
    active_raw = filter_active_slate(raw, app_sport)
    window_start, window_end, window_days = active_window(app_sport)
    window_payload = {
        "activeWindowStart": window_start.isoformat(),
        "activeWindowEnd": window_end.isoformat(),
        "slateWindowDays": window_days,
        "activeWindowBackBufferHours": ACTIVE_WINDOW_BACK_BUFFER_HOURS,
    }
    if not active_raw:
        return {
            "ok": False,
            "appSport": app_sport,
            "providerSportKey": provider_sport_key,
            "rawGamesReturned": raw_count,
            "rawGamesInWindow": 0,
            "gamesStored": 0,
            "stored": None,
            "error": "active_slate_window_empty",
            **window_payload,
        }
    games = [g for g in (convert_game(item, app_sport, provider_sport_key) for item in active_raw) if g]
    if not games:
        return {
            "ok": False,
            "appSport": app_sport,
            "providerSportKey": provider_sport_key,
            "rawGamesReturned": raw_count,
            "rawGamesInWindow": len(active_raw),
            "gamesStored": 0,
            "stored": None,
            "error": "active_slate_games_missing_supported_books_or_markets",
            **window_payload,
        }
    payload = {
        "sport": app_sport,
        "pulled_at": now(),
        "slate_date": today(),
        "source": "the_odds_api",
        "interval_minutes": 15,
        "provider_sport_key": provider_sport_key,
        "games": games,
        "meta": {
            "rawGamesReturned": raw_count,
            "rawGamesInWindow": len(active_raw),
            **window_payload,
        },
    }
    if inqsi_pull_history is None:
        return {"ok": False, "error": "pull_history_module_unavailable"}
    stored = inqsi_pull_history.store_pull(payload)
    return {
        "ok": bool(stored.get("ok")),
        "appSport": app_sport,
        "providerSportKey": provider_sport_key,
        "rawGamesReturned": raw_count,
        "rawGamesInWindow": len(active_raw),
        "gamesStored": len(games),
        "stored": stored.get("stored"),
        "error": stored.get("error"),
        **window_payload,
    }


def pull_sport(app_sport: str) -> Dict[str, Any]:
    app_sport = sport_key(app_sport)
    results = []
    for provider_key in provider_keys_for(app_sport):
        try:
            results.append(pull_one(app_sport, provider_key))
        except Exception as exc:
            results.append({"ok": False, "appSport": app_sport, "providerSportKey": provider_key, "error": safe_error(exc)})
    games_stored = sum(int(r.get("gamesStored") or 0) for r in results)
    raw_games = sum(int(r.get("rawGamesReturned") or 0) for r in results if isinstance(r.get("rawGamesReturned"), int))
    in_window = sum(int(r.get("rawGamesInWindow") or 0) for r in results if isinstance(r.get("rawGamesInWindow"), int))
    return {"ok": any(r.get("ok") for r in results), "appSport": app_sport, "rawGamesReturned": raw_games, "rawGamesInWindow": in_window, "gamesStored": games_stored, "providerPulls": results}


def pull_many(sports: List[str]) -> Dict[str, Any]:
    canonical_sports = [sport_key(s) for s in sports]
    results = [pull_sport(s) for s in canonical_sports]
    return {"ok": any(r.get("ok") for r in results), "sportsRequested": sports, "sportsCanonical": canonical_sports, "sportsPulled": len(results), "results": results}


def provider_status(probe: bool = False) -> Dict[str, Any]:
    status = {
        "ok": True,
        "provider": "the_odds_api",
        "oddsApiKeyPresent": bool(ODDS_API_KEY),
        "oddsApiKeyLength": len(ODDS_API_KEY or ""),
        "secretExposed": False,
        "regions": ODDS_REGIONS,
        "markets": ODDS_MARKETS,
        "oddsFormat": ODDS_FORMAT,
        "pullHistoryModuleReady": inqsi_pull_history is not None,
        "sportProviderMap": SPORT_PROVIDER_MAP,
        "activeSlateWindowsDays": DEFAULT_SLATE_WINDOW_DAYS,
        "activeWindowBackBufferHours": ACTIVE_WINDOW_BACK_BUFFER_HOURS,
    }
    if probe:
        try:
            sports = http_get_json(sports_url())
            status["providerProbeOk"] = True
            status["providerSportsReturned"] = len(sports) if isinstance(sports, list) else 0
            status["sampleProviderSports"] = [s.get("key") for s in sports[:20] if isinstance(s, dict)] if isinstance(sports, list) else []
        except Exception as exc:
            status["providerProbeOk"] = False
            status["providerError"] = safe_error(exc)
    return status


def route(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    event = event or {}
    method = (event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method") or "GET").upper()
    path = (event.get("rawPath") or event.get("path") or "/").rstrip("/") or "/"
    if method == "OPTIONS" and (path.startswith("/v1/inqsi/odds") or path.startswith("/v1/odds")):
        return out(200, {"ok": True})
    if path in {"/v1/inqsi/odds/provider-status", "/v1/odds/provider-status"} and method == "GET":
        p = params(event)
        return out(200, provider_status(str(p.get("probe") or "").lower() == "true"))
    if path in {"/v1/inqsi/odds/sports-map", "/v1/odds/sports-map"} and method == "GET":
        return out(200, {"ok": True, "defaultPullSequence": DEFAULT_PULL_SEQUENCE, "sportProviderMap": SPORT_PROVIDER_MAP, "activeSlateWindowsDays": DEFAULT_SLATE_WINDOW_DAYS})
    if path in {"/v1/inqsi/odds/pull", "/v1/odds/pull"} and method in {"GET", "POST"}:
        p = params(event)
        sport = p.get("sport") or p.get("sport_key")
        if str(p.get("all") or "").lower() == "true":
            sports = [s.strip() for s in str(p.get("sports") or ",".join(DEFAULT_PULL_SEQUENCE)).split(",") if s.strip()]
            return out(200, pull_many(sports))
        if not sport:
            return out(400, {"ok": False, "error": "sport_required", "defaultPullSequence": DEFAULT_PULL_SEQUENCE})
        return out(200, pull_sport(str(sport)))
    return None


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    routed = route(event)
    return routed or out(404, {"ok": False, "error": "not_found"})
