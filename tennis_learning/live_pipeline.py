from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Iterable, Mapping

import boto3

from handler import predict, settle, status

TABLE_NAME = os.environ["TENNIS_LEARNING_TABLE"]
ODDS_API_KEY = os.environ["ODDS_API_KEY"]
SPORT_KEY = os.getenv("TENNIS_ODDS_SPORT_KEY", "tennis")
BASE_URL = os.getenv("TENNIS_ODDS_BASE_URL", "https://api.the-odds-api.com/v4")
table = boto3.resource("dynamodb").Table(TABLE_NAME)


def _get(path: str, params: Mapping[str, Any]) -> Any:
    query = dict(params)
    query["apiKey"] = ODDS_API_KEY
    url = f"{BASE_URL}{path}?{urllib.parse.urlencode(query)}"
    request = urllib.request.Request(url, headers={"User-Agent": "inqis-tennis-learning/1.0"})
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.loads(response.read().decode("utf-8"))


def _best_h2h(event: Mapping[str, Any]) -> tuple[float, float] | None:
    names = [str(event.get("home_team") or ""), str(event.get("away_team") or "")]
    prices: Dict[str, list[float]] = {names[0]: [], names[1]: []}
    for book in event.get("bookmakers") or []:
        for market in book.get("markets") or []:
            if market.get("key") != "h2h":
                continue
            for outcome in market.get("outcomes") or []:
                name = str(outcome.get("name") or "")
                if name in prices:
                    try:
                        prices[name].append(float(outcome["price"]))
                    except (KeyError, TypeError, ValueError):
                        pass
    if not prices[names[0]] or not prices[names[1]]:
        return None
    return max(prices[names[0]]), max(prices[names[1]])


def collect_live() -> Dict[str, Any]:
    events = _get(f"/sports/{SPORT_KEY}/odds", {"regions": "us,uk,eu", "markets": "h2h", "oddsFormat": "american", "dateFormat": "iso"})
    stored = predicted = skipped = 0
    now = datetime.now(timezone.utc).isoformat()
    for event in events:
        pair = _best_h2h(event)
        if pair is None:
            skipped += 1
            continue
        player = str(event.get("home_team") or "")
        opponent = str(event.get("away_team") or "")
        event_id = str(event.get("id") or "")
        if not player or not opponent or not event_id:
            skipped += 1
            continue
        signals = {
            "player_odds": pair[0], "opponent_odds": pair[1],
            "elo_diff": 0.0, "surface_elo_diff": 0.0,
            "recent_win_rate_diff": 0.0, "serve_points_won_diff": 0.0,
            "return_points_won_diff": 0.0, "break_points_saved_diff": 0.0,
            "rest_days_diff": 0.0, "best_of_five": False,
        }
        table.put_item(Item={
            "PK": f"LIVE#{event_id}", "SK": "LATEST", "event_id": event_id,
            "player": player, "opponent": opponent,
            "commence_time": str(event.get("commence_time") or ""),
            "signals": {k: Decimal(str(v)) if isinstance(v, (int, float)) else v for k, v in signals.items()},
            "source": "the-odds-api-v4", "updated_at": now,
        })
        predict({"match_id": event_id, "player": player, "opponent": opponent, "signals": signals})
        stored += 1
        predicted += 1
    return {"events": len(events), "stored": stored, "predicted": predicted, "skipped": skipped, "model": status()}


def _winner(score_event: Mapping[str, Any]) -> str | None:
    if not score_event.get("completed"):
        return None
    scores = score_event.get("scores") or []
    if len(scores) != 2:
        return None
    try:
        first = int(scores[0]["score"])
        second = int(scores[1]["score"])
    except (KeyError, TypeError, ValueError):
        return None
    if first == second:
        return None
    return str(scores[0]["name"] if first > second else scores[1]["name"])


def settle_recent() -> Dict[str, Any]:
    events = _get(f"/sports/{SPORT_KEY}/scores/", {"daysFrom": 3, "dateFormat": "iso"})
    trained = duplicates = missing = 0
    for event in events:
        winner = _winner(event)
        event_id = str(event.get("id") or "")
        if not winner or not event_id:
            continue
        item = table.get_item(Key={"PK": f"LIVE#{event_id}", "SK": "LATEST"}, ConsistentRead=True).get("Item")
        if not item:
            missing += 1
            continue
        player = str(item["player"])
        signals = {k: float(v) if isinstance(v, Decimal) else v for k, v in dict(item["signals"]).items()}
        result = settle({
            "match_id": event_id, "player": player, "opponent": str(item["opponent"]),
            "event_time": str(event.get("commence_time") or item.get("commence_time") or datetime.now(timezone.utc).isoformat()),
            "player_won": winner == player, "signals": signals,
            "source": "the-odds-api-v4-scores", "source_mode": "live_settlement",
        })
        trained += int(result.get("trained", False))
        duplicates += int(result.get("duplicate", False))
    return {"score_events": len(events), "trained": trained, "duplicates": duplicates, "missing_snapshots": missing, "model": status()}


def lambda_handler(event: Mapping[str, Any], context: Any) -> Dict[str, Any]:
    action = str(event.get("action") or "collect")
    result = settle_recent() if action == "settle" else collect_live()
    return {"statusCode": 200, "headers": {"content-type": "application/json"}, "body": json.dumps(result, default=str)}
