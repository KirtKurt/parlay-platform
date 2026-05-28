import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import boto3

from audit_ledger import record_no_edge_prediction_rows, record_snapshot_audit


dynamodb = boto3.resource("dynamodb")
SNAPSHOTS_TABLE = os.environ.get("SNAPSHOTS_TABLE", "")
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
snapshots_tbl = dynamodb.Table(SNAPSHOTS_TABLE) if SNAPSHOTS_TABLE else None

SPORT_KEY = "baseball_mlb"
ODDS_MARKETS = "h2h,spreads,totals"
DEFAULT_DAYS_AHEAD = 1  # Today + tomorrow, so next-day MLB lines are captured as soon as available.


def _ddb_safe(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _ddb_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_ddb_safe(v) for v in value]
    return value


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


def _resp(status: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json",
            "access-control-allow-origin": "*",
            "access-control-allow-headers": "content-type",
            "access-control-allow-methods": "POST,OPTIONS",
        },
        "body": json.dumps(body, default=_json_default),
    }


def _parse_json(body: Optional[str]) -> Dict[str, Any]:
    if not body:
        return {}
    try:
        parsed = json.loads(body)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _event_payload(event: Dict[str, Any]) -> Dict[str, Any]:
    body = _parse_json(event.get("body"))
    if body:
        return body
    return event if isinstance(event, dict) else {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slate_date_et() -> str:
    return datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")


def _game_date_et(commence_time: Optional[str]) -> Optional[str]:
    if not commence_time:
        return None
    try:
        dt = datetime.fromisoformat(commence_time.replace("Z", "+00:00"))
    except Exception:
        return None
    return dt.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")


def _http_get_json(url: str, timeout: int = 20) -> Any:
    req = urllib.request.Request(url, headers={"accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _odds_url() -> str:
    if not ODDS_API_KEY:
        raise RuntimeError("ODDS_API_KEY missing")
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "us",
        "markets": ODDS_MARKETS,
        "oddsFormat": "american",
        "dateFormat": "iso",
    }
    return f"https://api.the-odds-api.com/v4/sports/{SPORT_KEY}/odds/?" + urllib.parse.urlencode(params)


def _oddsapi_auth_diagnostic(exc: Exception) -> Optional[Dict[str, Any]]:
    """Return a non-secret diagnostic when The Odds API blocks the live pull."""
    if isinstance(exc, urllib.error.HTTPError):
        try:
            upstream_body = exc.read().decode("utf-8")[:300]
        except Exception:
            upstream_body = ""
        return {
            "source": "theOddsAPI",
            "http_status": exc.code,
            "reason": exc.reason,
            "message": "Live MLB odds pull was rejected by the upstream odds feed.",
            "odds_api_key_present": bool(ODDS_API_KEY),
            "odds_api_key_length": len(ODDS_API_KEY or ""),
            "secret_exposed": False,
            "upstream_body_sample": upstream_body,
        }
    if str(exc) == "ODDS_API_KEY missing":
        return {
            "source": "theOddsAPI",
            "http_status": None,
            "reason": "missing_key",
            "message": "ODDS_API_KEY is not configured on the deployed Lambda.",
            "odds_api_key_present": False,
            "odds_api_key_length": 0,
            "secret_exposed": False,
        }
    return None


def _transparent_cached_pre_start_response(payload: Dict[str, Any], live_pull_error: Exception) -> Optional[Dict[str, Any]]:
    """
    For pre-start display runs, keep the product usable when the external feed rejects
    the refresh by explicitly returning the latest stored MLB snapshot analysis.

    This is not silent fallback: live_pull_ok=false and the upstream diagnostic are
    included in the response body. If cached analysis cannot be built, the caller gets
    the original failure.
    """
    run = str(payload.get("run") or "")
    if "pre_start" not in run and "final" not in run:
        return None

    diagnostic = _oddsapi_auth_diagnostic(live_pull_error)
    if not diagnostic:
        return None

    try:
        from mlb_signal_api import hot_sides

        cached = hot_sides(limit=80, store=True, include_no_edge=True)
        return {
            **cached,
            "ok": True,
            "sport": "mlb",
            "live_pull_ok": False,
            "fallback_used": True,
            "fallback_type": "latest_stored_snapshots_after_live_pull_auth_failure",
            "source_status": {
                "live_odds_feed": "AUTH_FAILED",
                "cached_snapshot_analysis": "USED",
                "predictions_storage": cached.get("storage_status"),
            },
            "upstream_error": diagnostic,
            "message": (
                "Live MLB odds refresh failed because the upstream odds feed rejected authorization. "
                "Returned latest stored MLB game-winner attempts and 3-leg parlay so the front end still has a transparent pre-start card. "
                "Fix ODDS_API_KEY in AWS/GitHub secrets to restore fresh live pulls."
            ),
        }
    except Exception as fallback_exc:
        return {
            "ok": False,
            "sport": "mlb",
            "live_pull_ok": False,
            "fallback_used": False,
            "source_status": {
                "live_odds_feed": "AUTH_FAILED",
                "cached_snapshot_analysis": "FAILED",
            },
            "upstream_error": diagnostic,
            "fallback_error": str(fallback_exc),
            "message": "Live pull failed and cached pre-start analysis could not be generated.",
        }


def _filter_upcoming_et(games: List[Dict[str, Any]], *, start_date: str, days_ahead: int) -> List[Dict[str, Any]]:
    """Keep games from ET start_date through start_date + days_ahead.

    Permanent MLB behavior: pull today + tomorrow every 15 minutes so next-day lines
    are captured immediately when books/the feed first publish ML, spread, and total.
    """
    eastern = ZoneInfo("America/New_York")
    start = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=eastern)
    allowed = {(start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(max(0, days_ahead) + 1)}
    out = []
    for game in games or []:
        game_date = _game_date_et(game.get("commence_time"))
        if game_date in allowed:
            out.append(game)
    return out


def _market(bookmaker: Dict[str, Any], key: str) -> Optional[Dict[str, Any]]:
    return next((m for m in bookmaker.get("markets", []) or [] if m.get("key") == key), None)


def _h2h(bookmaker: Dict[str, Any], home: str, away: str) -> Optional[Dict[str, int]]:
    market = _market(bookmaker, "h2h")
    if not market:
        return None
    home_price = away_price = None
    for outcome in market.get("outcomes", []) or []:
        if outcome.get("name") == home:
            home_price = outcome.get("price")
        elif outcome.get("name") == away:
            away_price = outcome.get("price")
    if home_price is None or away_price is None:
        return None
    return {"home": int(home_price), "away": int(away_price)}


def _spread(bookmaker: Dict[str, Any], home: str, away: str) -> Optional[Dict[str, Any]]:
    market = _market(bookmaker, "spreads")
    if not market:
        return None
    result: Dict[str, Any] = {}
    for outcome in market.get("outcomes", []) or []:
        if outcome.get("name") == home:
            result["home_point"] = outcome.get("point")
            result["home_price"] = outcome.get("price")
        elif outcome.get("name") == away:
            result["away_point"] = outcome.get("point")
            result["away_price"] = outcome.get("price")
    return result if len(result) == 4 else None


def _total(bookmaker: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    market = _market(bookmaker, "totals")
    if not market:
        return None
    result: Dict[str, Any] = {}
    for outcome in market.get("outcomes", []) or []:
        name = (outcome.get("name") or "").lower()
        if name == "over":
            result["over_point"] = outcome.get("point")
            result["over_price"] = outcome.get("price")
        elif name == "under":
            result["under_point"] = outcome.get("point")
            result["under_price"] = outcome.get("price")
    return result if len(result) == 4 else None


def _compact(raw_games: List[Dict[str, Any]]) -> Dict[str, Any]:
    games_out = []
    books_seen = set()
    game_dates_seen = set()
    for raw_game in raw_games or []:
        home = raw_game.get("home_team")
        away = raw_game.get("away_team")
        if not home or not away:
            continue
        game_date = _game_date_et(raw_game.get("commence_time"))
        if not game_date:
            continue
        game_dates_seen.add(game_date)
        books: Dict[str, Any] = {}
        for bookmaker in raw_game.get("bookmakers", []) or []:
            book_key = (bookmaker.get("key") or "").lower().strip()
            if not book_key:
                continue
            payload: Dict[str, Any] = {}
            ml = _h2h(bookmaker, home, away)
            spread = _spread(bookmaker, home, away)
            total = _total(bookmaker)
            if ml:
                payload["ml"] = ml
            if spread:
                payload["spread"] = spread
            if total:
                payload["total"] = total
            if payload:
                books[book_key] = payload
                books_seen.add(book_key)
        game_key = f"mlb|{game_date}|{away.lower()}|{home.lower()}"
        games_out.append({
            "id": raw_game.get("id") or game_key,
            "game_key": game_key,
            "internal_key": game_key,
            "game_date_et": game_date,
            "commence_time": raw_game.get("commence_time"),
            "home_team": home,
            "away_team": away,
            "books": books,
            "markets_stored": ["ml", "spread", "total"],
        })
    return {
        "games": games_out,
        "count": len(games_out),
        "game_dates_et": sorted(game_dates_seen),
        "available_book_keys": sorted(books_seen),
        "markets": ["ml", "spread", "total"],
    }


def lambda_handler(event, context):
    event = event or {}
    if (event.get("httpMethod") or "").upper() == "OPTIONS":
        return _resp(200, {"ok": True})
    try:
        payload = _event_payload(event)
        t = payload.get("t") or "HOT"
        run = payload.get("run") or "rolling_open_hot_pull"
        days_ahead = int(payload.get("days_ahead", DEFAULT_DAYS_AHEAD))
        slate_date = _slate_date_et()
        asof = _now_iso()
        raw = _filter_upcoming_et(_http_get_json(_odds_url()), start_date=slate_date, days_ahead=days_ahead)
        compact = _compact(raw)
        if snapshots_tbl is None:
            raise RuntimeError("SNAPSHOTS_TABLE not configured")
        date_scope = "_".join(compact.get("game_dates_et") or [slate_date])
        slate_id = f"MLB_ROLLING_{date_scope}_{run}"
        item = {
            "PK": "SPORT#mlb",
            "SK": f"{t}#DATE#{slate_date}#ASOF#{asof}#SLATE#{slate_id}",
            "sport": "mlb",
            "t": t,
            "slate_id": slate_id,
            "slate_date_et": slate_date,
            "game_dates_et": compact.get("game_dates_et") or [],
            "rolling_open_pull": True,
            "days_ahead": days_ahead,
            "asof": asof,
            "created_at": asof,
            "data": compact,
            "meta": {
                "source": "theOddsAPI",
                "run_type": run,
                "pulled_at": asof,
                "markets": ["h2h", "spreads", "totals"],
                "pull_policy": "rolling_open_today_plus_tomorrow_every_15_min",
                "purpose": "Capture early MLB movement immediately when next-day lines become available.",
            },
        }
        item = _ddb_safe(item)
        snapshots_tbl.put_item(Item=item)
        audit_result = record_snapshot_audit(sport="mlb", slate_date_et=slate_date, asof=asof, t=t, run_type=run, compact_snapshot=compact, raw_games=raw)
        prediction_audit_result = record_no_edge_prediction_rows(sport="mlb", slate_date_et=slate_date, asof=asof, compact_snapshot=compact)
        return _resp(200, {
            "ok": True,
            "sport": "mlb",
            "live_pull_ok": True,
            "fallback_used": False,
            "t": t,
            "run": run,
            "pull_policy": "rolling_open_today_plus_tomorrow_every_15_min",
            "slate_date_et": slate_date,
            "game_dates_et": compact.get("game_dates_et") or [],
            "days_ahead": days_ahead,
            "asof": asof,
            "count": compact["count"],
            "stored": {"pk": item["PK"], "sk": item["SK"]},
            "available_book_keys": compact["available_book_keys"],
            "markets": compact["markets"],
            "audit": audit_result,
            "prediction_audit": prediction_audit_result,
        })
    except Exception as exc:
        payload = _event_payload(event)
        fallback = _transparent_cached_pre_start_response(payload, exc)
        if fallback is not None:
            return _resp(200 if fallback.get("ok") else 500, fallback)
        return _resp(500, {
            "ok": False,
            "sport": "mlb",
            "live_pull_ok": False,
            "fallback_used": False,
            "upstream_error": _oddsapi_auth_diagnostic(exc),
            "error": str(exc),
        })
