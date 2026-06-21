import os
import urllib.parse
import urllib.request
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

import boto3
from boto3.dynamodb.conditions import Key


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


ODDS_API_KEY = _env("ODDS_API_KEY")
ODDS_REGIONS = _env("ODDS_REGIONS", "us")
ODDS_MARKETS = _env("ODDS_MARKETS", "h2h,spreads,totals")
ODDS_FORMAT = _env("ODDS_FORMAT", "american")
SNAPSHOTS_TABLE = _env("SNAPSHOTS_TABLE")
SIGNAL_LEDGER_TABLE = _env("SIGNAL_LEDGER_TABLE")

_dynamodb = boto3.resource("dynamodb")
_snapshots = _dynamodb.Table(SNAPSHOTS_TABLE) if SNAPSHOTS_TABLE else None
_signal_ledger = _dynamodb.Table(SIGNAL_LEDGER_TABLE) if SIGNAL_LEDGER_TABLE else None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_ddb(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(round(value, 6)))
    if isinstance(value, dict):
        return {k: _to_ddb(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_ddb(v) for v in value]
    return value


def _from_ddb(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: _from_ddb(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_from_ddb(v) for v in value]
    return value


def _http_get_json(path: str, params: Dict[str, Any], timeout: int = 25) -> Any:
    if not ODDS_API_KEY:
        raise RuntimeError("ODDS_API_KEY missing")
    merged = {"apiKey": ODDS_API_KEY, **params}
    url = "https://api.the-odds-api.com/v4" + path + "?" + urllib.parse.urlencode(merged)
    request = urllib.request.Request(url, headers={"accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _latest_game_states(sport_key: str) -> Dict[str, Dict[str, Any]]:
    if _signal_ledger is None:
        return {}
    response = _signal_ledger.query(KeyConditionExpression=Key("PK").eq(f"INQSI#LATEST#{sport_key}"))
    return {item.get("game_id"): _from_ddb(item) for item in response.get("Items", []) if item.get("game_id")}


def _extract_live_odds(raw_game: Dict[str, Any]) -> Dict[str, Any]:
    books = {}
    for book in raw_game.get("bookmakers", []) or []:
        book_key = (book.get("key") or "").lower().strip()
        if not book_key:
            continue
        payload = {"title": book.get("title"), "last_update": book.get("last_update")}
        for market in book.get("markets", []) or []:
            key = market.get("key")
            if key == "h2h":
                payload["moneyline"] = market.get("outcomes", [])
            elif key == "spreads":
                payload["spread"] = market.get("outcomes", [])
            elif key == "totals":
                payload["total"] = market.get("outcomes", [])
        if any(k in payload for k in ["moneyline", "spread", "total"]):
            books[book_key] = payload
    return books


def pull_scores_for_sport(sport_key: str, days_from: int = 1) -> List[Dict[str, Any]]:
    data = _http_get_json(f"/sports/{sport_key}/scores/", {"daysFrom": days_from, "dateFormat": "iso"})
    return data if isinstance(data, list) else []


def pull_live_odds_for_sport(sport_key: str) -> List[Dict[str, Any]]:
    data = _http_get_json(
        f"/sports/{sport_key}/odds/",
        {"regions": ODDS_REGIONS, "markets": ODDS_MARKETS, "oddsFormat": ODDS_FORMAT, "dateFormat": "iso"},
    )
    return data if isinstance(data, list) else []


def _score_status(score_game: Dict[str, Any]) -> str:
    if score_game.get("completed") is True:
        return "final"
    scores = score_game.get("scores") or []
    if scores:
        return "live"
    return "scheduled"


def ingest_live_sport(sport_key: str) -> Dict[str, Any]:
    if _signal_ledger is None:
        raise RuntimeError("SIGNAL_LEDGER_TABLE not configured")
    asof = now_iso()
    scores = pull_scores_for_sport(sport_key)
    live_odds = {g.get("id"): g for g in pull_live_odds_for_sport(sport_key) if g.get("id")}
    latest_states = _latest_game_states(sport_key)
    written = 0
    live_count = 0
    for score_game in scores:
        game_id = score_game.get("id")
        if not game_id:
            continue
        status = _score_status(score_game)
        if status == "live":
            live_count += 1
        odds_game = live_odds.get(game_id, {})
        state = latest_states.get(game_id, {})
        item = {
            "PK": f"INQSI#LIVE#{sport_key}",
            "SK": f"GAME#{game_id}#ASOF#{asof}",
            "entity_type": "INQSI_LIVE_GAME_STATE",
            "sport_key": sport_key,
            "game_id": game_id,
            "asof": asof,
            "poll_interval_minutes": 3,
            "live_status": status,
            "commence_time": score_game.get("commence_time") or odds_game.get("commence_time") or state.get("commence_time"),
            "home_team": score_game.get("home_team") or odds_game.get("home_team") or state.get("home_team"),
            "away_team": score_game.get("away_team") or odds_game.get("away_team") or state.get("away_team"),
            "scores": score_game.get("scores", []),
            "completed": score_game.get("completed", False),
            "live_odds_by_book": _extract_live_odds(odds_game) if odds_game else {},
            "latest_signal_score": state.get("signal_score"),
            "latest_stability_classification": state.get("stability_classification"),
            "latest_primary_signal": state.get("primary_signal"),
        }
        _signal_ledger.put_item(Item=_to_ddb(item))
        _signal_ledger.put_item(Item=_to_ddb({**item, "PK": f"INQSI#LIVE_LATEST#{sport_key}", "SK": f"GAME#{game_id}", "entity_type": "INQSI_LIVE_LATEST_GAME_STATE"}))
        written += 1
    return {"ok": True, "sport_key": sport_key, "asof": asof, "games_checked": len(scores), "live_games": live_count, "records_written": written}


def latest_live_games(sport_key: str) -> Dict[str, Any]:
    if _signal_ledger is None:
        raise RuntimeError("SIGNAL_LEDGER_TABLE not configured")
    response = _signal_ledger.query(KeyConditionExpression=Key("PK").eq(f"INQSI#LIVE_LATEST#{sport_key}"))
    games = [_from_ddb(item) for item in response.get("Items", [])]
    return {"ok": True, "sport_key": sport_key, "games": games, "count": len(games)}
