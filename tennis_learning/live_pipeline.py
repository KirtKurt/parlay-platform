from __future__ import annotations

import json
import math
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Iterable, Mapping

import boto3
from boto3.dynamodb.conditions import Attr

from handler import predict, settle, status

TABLE_NAME = os.environ["TENNIS_LEARNING_TABLE"]
ODDS_API_KEY = os.environ["ODDS_API_KEY"]
BASE_URL = os.getenv("TENNIS_ODDS_BASE_URL", "https://api.the-odds-api.com/v4")
REGIONS = os.getenv("TENNIS_ODDS_REGIONS", "us")
MAX_ACTIVE_KEYS = int(os.getenv("TENNIS_MAX_ACTIVE_KEYS", "24"))
table = boto3.resource("dynamodb").Table(TABLE_NAME)


def _get(path: str, params: Mapping[str, Any]) -> Any:
    query = dict(params)
    query["apiKey"] = ODDS_API_KEY
    url = f"{BASE_URL}{path}?{urllib.parse.urlencode(query)}"
    request = urllib.request.Request(url, headers={"User-Agent": "inqis-tennis-learning/1.3"})
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.loads(response.read().decode("utf-8"))


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _decimal(value: Any) -> Decimal:
    number = _finite(value)
    if number is None:
        raise ValueError(f"non-finite numeric value: {value!r}")
    return Decimal(str(number))


def _discover_tennis_keys() -> list[str]:
    configured = [x.strip() for x in os.getenv("TENNIS_ODDS_SPORT_KEYS", "").split(",") if x.strip() and x.strip() != "tennis"]
    if configured:
        return configured[:MAX_ACTIVE_KEYS]
    sports = _get("/sports/", {"all": "false"})
    keys = []
    for sport in sports if isinstance(sports, list) else []:
        key = str(sport.get("key") or "")
        if key.startswith("tennis_") and str(sport.get("group") or "").lower() == "tennis" and bool(sport.get("active", False)) and not bool(sport.get("has_outrights", False)):
            keys.append(key)
    return sorted(set(keys))[:MAX_ACTIVE_KEYS]


def _eventful_keys(keys: Iterable[str]) -> tuple[list[str], Dict[str, str]]:
    active, errors = [], {}
    for key in keys:
        try:
            events = _get(f"/sports/{key}/events", {"dateFormat": "iso"})
            if isinstance(events, list) and events:
                active.append(key)
        except Exception as exc:
            errors[key] = str(exc)
    return active, errors


def _best_h2h(event: Mapping[str, Any]) -> tuple[float, float] | None:
    names = [str(event.get("home_team") or ""), str(event.get("away_team") or "")]
    if not all(names) or names[0] == names[1]:
        return None
    prices: Dict[str, list[float]] = {names[0]: [], names[1]: []}
    for book in event.get("bookmakers") or []:
        for market in book.get("markets") or []:
            if market.get("key") != "h2h":
                continue
            for outcome in market.get("outcomes") or []:
                name = str(outcome.get("name") or "")
                price = _finite(outcome.get("price"))
                if name in prices and price is not None and price != 0:
                    prices[name].append(price)
    if not prices[names[0]] or not prices[names[1]]:
        return None
    pair = max(prices[names[0]]), max(prices[names[1]])
    return pair if all(math.isfinite(x) and x != 0 for x in pair) else None


def collect_live() -> Dict[str, Any]:
    discovered = _discover_tennis_keys()
    eventful, discovery_errors = _eventful_keys(discovered)
    stored = predicted = skipped = malformed = total_events = successful_keys = 0
    request_errors: Dict[str, str] = dict(discovery_errors)
    now = datetime.now(timezone.utc).isoformat()
    for sport_key in eventful:
        try:
            events = _get(f"/sports/{sport_key}/odds", {"regions": REGIONS, "markets": "h2h", "oddsFormat": "american", "dateFormat": "iso"})
            successful_keys += 1
        except Exception as exc:
            request_errors[sport_key] = str(exc)
            continue
        if not isinstance(events, list):
            request_errors[sport_key] = "unexpected odds response"
            continue
        total_events += len(events)
        for event in events:
            pair = _best_h2h(event)
            player = str(event.get("home_team") or "")
            opponent = str(event.get("away_team") or "")
            event_id = str(event.get("id") or "")
            if pair is None or not player or not opponent or not event_id:
                skipped += 1
                continue
            signals = {"player_odds": pair[0], "opponent_odds": pair[1], "elo_diff": 0.0, "surface_elo_diff": 0.0, "recent_win_rate_diff": 0.0, "serve_points_won_diff": 0.0, "return_points_won_diff": 0.0, "break_points_saved_diff": 0.0, "rest_days_diff": 0.0, "best_of_five": False}
            try:
                safe_signals = {k: (_decimal(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v) for k, v in signals.items()}
                table.put_item(Item={"PK": f"LIVE#{event_id}", "SK": "LATEST", "event_id": event_id, "sport_key": sport_key, "player": player, "opponent": opponent, "commence_time": str(event.get("commence_time") or ""), "signals": safe_signals, "source": "the-odds-api-v4", "updated_at": now})
                predict({"match_id": event_id, "player": player, "opponent": opponent, "signals": signals})
            except (ValueError, ArithmeticError) as exc:
                malformed += 1
                request_errors[f"event:{event_id}"] = str(exc)
                continue
            stored += 1
            predicted += 1
    if eventful and successful_keys == 0:
        raise RuntimeError(f"all active tennis odds requests failed: {request_errors}")
    return {"discovered_sport_keys": len(discovered), "eventful_sport_keys": len(eventful), "successful_sport_keys": successful_keys, "events": total_events, "stored": stored, "predicted": predicted, "skipped": skipped, "malformed": malformed, "errors": request_errors, "model": status()}


def _winner(score_event: Mapping[str, Any]) -> str | None:
    if not score_event.get("completed"):
        return None
    scores = score_event.get("scores") or []
    if len(scores) != 2:
        return None
    try:
        first, second = int(scores[0]["score"]), int(scores[1]["score"])
    except (KeyError, TypeError, ValueError):
        return None
    if first == second:
        return None
    return str(scores[0]["name"] if first > second else scores[1]["name"])


def _stored_sport_keys() -> list[str]:
    keys: set[str] = set()
    kwargs: Dict[str, Any] = {"FilterExpression": Attr("PK").begins_with("LIVE#"), "ProjectionExpression": "sport_key"}
    while True:
        page = table.scan(**kwargs)
        for item in page.get("Items", []):
            key = str(item.get("sport_key") or "")
            if key.startswith("tennis_"):
                keys.add(key)
        cursor = page.get("LastEvaluatedKey")
        if not cursor:
            break
        kwargs["ExclusiveStartKey"] = cursor
    return sorted(keys)


def settle_recent() -> Dict[str, Any]:
    sport_keys = _stored_sport_keys()
    trained = duplicates = missing = score_events = successful_keys = malformed = 0
    errors: Dict[str, str] = {}
    for sport_key in sport_keys:
        try:
            events = _get(f"/sports/{sport_key}/scores/", {"daysFrom": 3, "dateFormat": "iso"})
            successful_keys += 1
        except Exception as exc:
            errors[sport_key] = str(exc)
            continue
        if not isinstance(events, list):
            errors[sport_key] = "unexpected scores response"
            continue
        score_events += len(events)
        for event in events:
            winner, event_id = _winner(event), str(event.get("id") or "")
            if not winner or not event_id:
                continue
            item = table.get_item(Key={"PK": f"LIVE#{event_id}", "SK": "LATEST"}, ConsistentRead=True).get("Item")
            if not item:
                missing += 1
                continue
            try:
                signals = {k: (float(v) if isinstance(v, Decimal) else v) for k, v in dict(item["signals"]).items()}
                if any(_finite(v) is None for k, v in signals.items() if k != "best_of_five"):
                    raise ValueError("stored signals contain non-finite values")
                result = settle({"match_id": event_id, "player": str(item["player"]), "opponent": str(item["opponent"]), "event_time": str(event.get("commence_time") or item.get("commence_time") or datetime.now(timezone.utc).isoformat()), "player_won": winner == str(item["player"]), "signals": signals, "source": "the-odds-api-v4-scores", "source_mode": "live_settlement"})
            except (ValueError, ArithmeticError) as exc:
                malformed += 1
                errors[f"event:{event_id}"] = str(exc)
                continue
            trained += int(result.get("trained", False))
            duplicates += int(result.get("duplicate", False))
    if sport_keys and successful_keys == 0:
        raise RuntimeError(f"all tracked tennis score requests failed: {errors}")
    return {"sport_keys": len(sport_keys), "successful_sport_keys": successful_keys, "score_events": score_events, "trained": trained, "duplicates": duplicates, "missing_snapshots": missing, "malformed": malformed, "errors": errors, "model": status()}


def lambda_handler(event: Mapping[str, Any], context: Any) -> Dict[str, Any]:
    action = str(event.get("action") or "collect")
    result = settle_recent() if action == "settle" else collect_live()
    return {"statusCode": 200, "headers": {"content-type": "application/json"}, "body": json.dumps(result, default=str)}
