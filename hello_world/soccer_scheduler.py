import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

import boto3

from soccer_audit import record_soccer_no_edge_prediction_rows, record_soccer_snapshot_audit


dynamodb = boto3.resource("dynamodb")
SNAPSHOTS_TABLE = os.environ.get("SNAPSHOTS_TABLE", "")
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")

snapshots_tbl = dynamodb.Table(SNAPSHOTS_TABLE) if SNAPSHOTS_TABLE else None

# Controlled starter-plan soccer list using only keys returned by live /v4/sports discovery.
# This expands beyond EPL/MLS because those returned 0 games, while keeping outrights excluded.
DEFAULT_SOCCER_KEYS = [
    "soccer_brazil_campeonato",
    "soccer_brazil_serie_b",
    "soccer_chile_campeonato",
    "soccer_china_superleague",
    "soccer_conmebol_copa_libertadores",
    "soccer_conmebol_copa_sudamericana",
    "soccer_finland_veikkausliiga",
    "soccer_japan_j_league",
    "soccer_league_of_ireland",
    "soccer_norway_eliteserien",
    "soccer_spain_segunda_division",
    "soccer_sweden_allsvenskan",
    "soccer_sweden_superettan",
]
SOCCER_KEYS = [s.strip() for s in os.environ.get("SOCCER_KEYS", ",".join(DEFAULT_SOCCER_KEYS)).split(",") if s.strip()]
ODDS_MARKETS = "h2h,spreads,totals"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slate_date_et() -> str:
    return datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")


def _http_get_json(url: str, timeout: int = 20) -> Any:
    req = urllib.request.Request(url, headers={"accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _odds_url(sport_key: str) -> str:
    if not ODDS_API_KEY:
        raise RuntimeError("ODDS_API_KEY missing")
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "us",
        "markets": ODDS_MARKETS,
        "oddsFormat": "american",
        "dateFormat": "iso",
    }
    return f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/?" + urllib.parse.urlencode(params)


def _compact_soccer_game(raw_game: Dict[str, Any], sport_key: str) -> Dict[str, Any]:
    home = raw_game.get("home_team")
    away = raw_game.get("away_team")
    game_id = raw_game.get("id") or f"{sport_key}|{away}|{home}|{raw_game.get('commence_time')}"
    books: Dict[str, Any] = {}

    for bookmaker in raw_game.get("bookmakers", []) or []:
        book_key = (bookmaker.get("key") or "").lower().strip()
        if not book_key:
            continue
        markets: Dict[str, Any] = {}
        for market in bookmaker.get("markets", []) or []:
            market_key = market.get("key")
            outcomes = []
            for outcome in market.get("outcomes", []) or []:
                row = {"name": outcome.get("name"), "price": outcome.get("price")}
                if "point" in outcome:
                    row["point"] = outcome.get("point")
                outcomes.append(row)
            if outcomes:
                markets[market_key] = outcomes
        if markets:
            books[book_key] = markets

    return {
        "id": game_id,
        "game_key": f"soccer|{sport_key}|{away}|{home}|{raw_game.get('commence_time')}",
        "sport": "soccer",
        "sport_key": sport_key,
        "commence_time": raw_game.get("commence_time"),
        "home_team": home,
        "away_team": away,
        "books": books,
        "markets_stored": ["h2h", "spreads", "totals"],
        "model_note": "Soccer is 3-way for h2h: home/draw/away. Keep isolated from 2-way sport models.",
    }


def pull_soccer_hot_snapshot() -> Dict[str, Any]:
    if snapshots_tbl is None:
        raise RuntimeError("SNAPSHOTS_TABLE not configured")

    asof = _now_iso()
    slate_date = _slate_date_et()
    games: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    raw_by_sport_key: Dict[str, List[Dict[str, Any]]] = {}

    for sport_key in SOCCER_KEYS:
        try:
            raw_games = _http_get_json(_odds_url(sport_key))
            raw_by_sport_key[sport_key] = raw_games or []
            for raw_game in raw_games or []:
                games.append(_compact_soccer_game(raw_game, sport_key))
        except Exception as exc:
            errors.append({"sport_key": sport_key, "error": str(exc)})

    compact_snapshot = {
        "games": games,
        "count": len(games),
        "soccer_keys": SOCCER_KEYS,
        "markets": ["h2h", "spreads", "totals"],
        "errors": errors,
    }
    item = {
        "PK": "SPORT#soccer",
        "SK": f"HOT#DATE#{slate_date}#ASOF#{asof}#SLATE#SOCCER_HOT",
        "sport": "soccer",
        "t": "HOT",
        "slate_id": f"SOCCER_{slate_date}_hot_pull",
        "slate_date_et": slate_date,
        "asof": asof,
        "created_at": asof,
        "data": compact_snapshot,
        "meta": {
            "source": "theOddsAPI",
            "run_type": "hot_pull_audited",
            "pulled_at": asof,
            "temporary_mode": "until_friday_starter_plan_baseball_plus_soccer_only_expanded_exact_keys",
            "soccer_model": "SOC-B1.1-three-way-audit-v1",
        },
    }
    snapshots_tbl.put_item(Item=item)
    audit_result = record_soccer_snapshot_audit(slate_date_et=slate_date, asof=asof, t="HOT", run_type="hot_pull_audited", compact_snapshot=compact_snapshot, raw_by_sport_key=raw_by_sport_key)
    prediction_audit = record_soccer_no_edge_prediction_rows(slate_date_et=slate_date, asof=asof, compact_snapshot=compact_snapshot)
    return {"ok": len(errors) == 0 and audit_result.get("ok", False), "sport": "soccer", "t": "HOT", "count": len(games), "soccer_keys": SOCCER_KEYS, "errors": errors, "audit": audit_result, "prediction_audit": prediction_audit}


def lambda_handler(event, context):
    try:
        return {"statusCode": 200, "headers": {"content-type": "application/json"}, "body": json.dumps(pull_soccer_hot_snapshot(), default=str)}
    except Exception as exc:
        return {"statusCode": 500, "headers": {"content-type": "application/json"}, "body": json.dumps({"ok": False, "sport": "soccer", "error": str(exc)})}
