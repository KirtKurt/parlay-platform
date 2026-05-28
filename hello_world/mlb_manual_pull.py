import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slate_date_et() -> str:
    return datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")


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


def _filter_today_et(games: List[Dict[str, Any]], slate_date: str) -> List[Dict[str, Any]]:
    eastern = ZoneInfo("America/New_York")
    out = []
    for game in games or []:
        commence = game.get("commence_time")
        if not commence:
            continue
        try:
            dt = datetime.fromisoformat(commence.replace("Z", "+00:00"))
        except Exception:
            continue
        if dt.astimezone(eastern).strftime("%Y-%m-%d") == slate_date:
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


def _compact(raw_games: List[Dict[str, Any]], slate_date: str) -> Dict[str, Any]:
    games_out = []
    books_seen = set()
    for raw_game in raw_games or []:
        home = raw_game.get("home_team")
        away = raw_game.get("away_team")
        if not home or not away:
            continue
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
        game_key = f"mlb|{slate_date}|{away.lower()}|{home.lower()}"
        games_out.append({
            "id": raw_game.get("id") or game_key,
            "game_key": game_key,
            "internal_key": game_key,
            "commence_time": raw_game.get("commence_time"),
            "home_team": home,
            "away_team": away,
            "books": books,
            "markets_stored": ["ml", "spread", "total"],
        })
    return {"games": games_out, "count": len(games_out), "available_book_keys": sorted(books_seen), "markets": ["ml", "spread", "total"]}


def lambda_handler(event, context):
    event = event or {}
    if (event.get("httpMethod") or "").upper() == "OPTIONS":
        return _resp(200, {"ok": True})
    try:
        body = _parse_json(event.get("body"))
        t = body.get("t") or "HOT"
        run = body.get("run") or "manual_hot_test"
        slate_date = _slate_date_et()
        asof = _now_iso()
        raw = _filter_today_et(_http_get_json(_odds_url()), slate_date)
        compact = _compact(raw, slate_date)
        if snapshots_tbl is None:
            raise RuntimeError("SNAPSHOTS_TABLE not configured")
        slate_id = f"MLB_{slate_date}_{run}"
        item = {
            "PK": "SPORT#mlb",
            "SK": f"{t}#DATE#{slate_date}#ASOF#{asof}#SLATE#{slate_id}",
            "sport": "mlb",
            "t": t,
            "slate_id": slate_id,
            "slate_date_et": slate_date,
            "asof": asof,
            "created_at": asof,
            "data": compact,
            "meta": {"source": "theOddsAPI", "run_type": run, "pulled_at": asof, "markets": ["h2h", "spreads", "totals"]},
        }
        item = _ddb_safe(item)
        snapshots_tbl.put_item(Item=item)
        audit_result = record_snapshot_audit(sport="mlb", slate_date_et=slate_date, asof=asof, t=t, run_type=run, compact_snapshot=compact, raw_games=raw)
        prediction_audit_result = record_no_edge_prediction_rows(sport="mlb", slate_date_et=slate_date, asof=asof, compact_snapshot=compact)
        return _resp(200, {
            "ok": True,
            "sport": "mlb",
            "t": t,
            "slate_date_et": slate_date,
            "asof": asof,
            "count": compact["count"],
            "stored": {"pk": item["PK"], "sk": item["SK"]},
            "available_book_keys": compact["available_book_keys"],
            "markets": compact["markets"],
            "audit": audit_result,
            "prediction_audit": prediction_audit_result,
        })
    except Exception as exc:
        return _resp(500, {"ok": False, "sport": "mlb", "error": str(exc)})
