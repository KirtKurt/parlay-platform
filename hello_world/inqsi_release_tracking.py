import os
from decimal import Decimal
from typing import Any, Dict, List, Optional

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


SNAPSHOTS_TABLE = _env("SNAPSHOTS_TABLE")
SIGNAL_LEDGER_TABLE = _env("SIGNAL_LEDGER_TABLE")

_dynamodb = boto3.resource("dynamodb")
_snapshots = _dynamodb.Table(SNAPSHOTS_TABLE) if SNAPSHOTS_TABLE else None
_signal_ledger = _dynamodb.Table(SIGNAL_LEDGER_TABLE) if SIGNAL_LEDGER_TABLE else None


def _from_ddb(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: _from_ddb(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_from_ddb(v) for v in value]
    return value


def _to_ddb(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(round(value, 6)))
    if isinstance(value, dict):
        return {k: _to_ddb(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_ddb(v) for v in value]
    return value


def latest_snapshot_for_sport(sport_key: str) -> Optional[Dict[str, Any]]:
    if _snapshots is None:
        raise RuntimeError("SNAPSHOTS_TABLE not configured")
    response = _snapshots.query(
        KeyConditionExpression=Key("PK").eq(f"INQSI#SPORT#{sport_key}") & Key("SK").begins_with("SNAPSHOT#"),
        ScanIndexForward=False,
        Limit=1,
    )
    items = response.get("Items", [])
    return _from_ddb(items[0]) if items else None


def _game_location(raw_game: Dict[str, Any]) -> Dict[str, Any]:
    location = raw_game.get("venue") or raw_game.get("location") or raw_game.get("site") or raw_game.get("arena") or raw_game.get("stadium")
    neutral_site = raw_game.get("neutral_site") if raw_game.get("neutral_site") is not None else raw_game.get("neutral")
    return {
        "venue": location,
        "neutral_site": neutral_site,
        "location_source": "provider" if location else "unavailable_from_provider",
    }


def upsert_game_metadata(sport_key: str, game: Dict[str, Any], asof: str) -> None:
    if _signal_ledger is None:
        return
    game_id = game["game_id"]
    metadata = {
        "PK": f"INQSI#GAME_META#{sport_key}",
        "SK": f"GAME#{game_id}",
        "entity_type": "INQSI_GAME_METADATA",
        "sport_key": sport_key,
        "game_id": game_id,
        "home_team": game.get("home_team"),
        "away_team": game.get("away_team"),
        "commence_time": game.get("commence_time"),
        "location": _game_location(game),
        "last_seen_at": asof,
        "available_books": game.get("available_books", []),
    }
    _signal_ledger.put_item(Item=_to_ddb(metadata))


def record_sportsbook_release(sport_key: str, game: Dict[str, Any], asof: str) -> int:
    if _signal_ledger is None:
        return 0
    created = 0
    game_id = game["game_id"]
    for book_key, book in (game.get("books") or {}).items():
        for market in ("moneyline", "spread", "total"):
            if market not in book:
                continue
            item = {
                "PK": f"INQSI#RELEASE#{sport_key}",
                "SK": f"GAME#{game_id}#BOOK#{book_key}#MARKET#{market}",
                "entity_type": "INQSI_SPORTSBOOK_MARKET_RELEASE",
                "sport_key": sport_key,
                "game_id": game_id,
                "home_team": game.get("home_team"),
                "away_team": game.get("away_team"),
                "commence_time": game.get("commence_time"),
                "book_key": book_key,
                "book_title": book.get("title"),
                "market": market,
                "first_seen_at": asof,
                "provider_last_update": book.get("last_update"),
                "initial_line": book.get(market),
            }
            try:
                _signal_ledger.put_item(Item=_to_ddb(item), ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)")
                created += 1
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                    raise
    return created


def record_release_tracking_for_sport(sport_key: str) -> Dict[str, Any]:
    snapshot = latest_snapshot_for_sport(sport_key)
    if not snapshot:
        return {"ok": True, "sport_key": sport_key, "message": "No snapshot available", "release_records_created": 0}
    asof = snapshot.get("asof")
    games: List[Dict[str, Any]] = snapshot.get("data", {}).get("games", [])
    release_count = 0
    for game in games:
        if game.get("sport_key") != sport_key:
            raise RuntimeError(f"Sport isolation violation: expected {sport_key}, got {game.get('sport_key')}")
        upsert_game_metadata(sport_key, game, asof)
        release_count += record_sportsbook_release(sport_key, game, asof)
    return {"ok": True, "sport_key": sport_key, "games_checked": len(games), "release_records_created": release_count}
