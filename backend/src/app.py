import json
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import boto3
from boto3.dynamodb.conditions import Key

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "content-type,authorization",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
}

ODDS_API_BASE_URL = "https://api.the-odds-api.com/v4"
SUPPORTED_SPORTS = {
    "NFL": "americanfootball_nfl",
    "CFB": "americanfootball_ncaaf",
    "NBA": "basketball_nba",
    "NCAAM": "basketball_ncaab",
    "NHL": "icehockey_nhl",
    "MLB": "baseball_mlb",
}
SPORT_KEY_TO_LABEL = {value: key for key, value in SUPPORTED_SPORTS.items()}
CANONICAL_BOOK_ORDER = ["fanatics", "fanduel", "draftkings"]

DDB = boto3.resource("dynamodb")
GAMES_TABLE = DDB.Table(os.environ["GAMES_TABLE"])
SNAPSHOTS_TABLE = DDB.Table(os.environ["SNAPSHOTS_TABLE"])
PARLAY_BUILDS_TABLE = DDB.Table(os.environ["PARLAY_BUILDS_TABLE"])
INGESTION_RUNS_TABLE = DDB.Table(os.environ["INGESTION_RUNS_TABLE"])


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def as_json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        if value % 1 == 0:
            return int(value)
        return float(value)
    if isinstance(value, list):
        return [as_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: as_json_safe(item) for key, item in value.items()}
    return value


def to_decimal_safe(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, list):
        return [to_decimal_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: to_decimal_safe(item) for key, item in value.items()}
    return value


def response(status_code: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": CORS_HEADERS,
        "body": json.dumps(as_json_safe(body)),
    }


def parse_body(event: Dict[str, Any]) -> Dict[str, Any]:
    raw_body = event.get("body")
    if not raw_body:
        return {}
    try:
        return json.loads(raw_body)
    except json.JSONDecodeError:
        return {}


def get_query(event: Dict[str, Any]) -> Dict[str, str]:
    return event.get("queryStringParameters") or {}


def fetch_odds_for_sport(sport_key: str) -> List[Dict[str, Any]]:
    api_key = os.environ.get("ODDS_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ODDS_API_KEY is not configured")

    params = {
        "apiKey": api_key,
        "regions": os.environ.get("ODDS_REGIONS", "us"),
        "markets": os.environ.get("ODDS_MARKETS", "h2h,spreads,totals"),
        "oddsFormat": "american",
        "dateFormat": "iso",
        "bookmakers": os.environ.get("ODDS_BOOKMAKERS", "fanduel,draftkings,fanatics"),
    }
    url = f"{ODDS_API_BASE_URL}/sports/{sport_key}/odds/?{urlencode(params)}"
    request = Request(url, headers={"User-Agent": "silvers-syndicate-api/1.0"})
    with urlopen(request, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def extract_bookmakers(event: Dict[str, Any]) -> List[Dict[str, Any]]:
    normalized_books: List[Dict[str, Any]] = []
    for bookmaker in event.get("bookmakers", []):
        book = {
            "key": bookmaker.get("key"),
            "title": bookmaker.get("title"),
            "last_update": bookmaker.get("last_update"),
            "markets": {},
        }
        for market in bookmaker.get("markets", []):
            key = market.get("key")
            outcomes = []
            for outcome in market.get("outcomes", []):
                outcomes.append(
                    {
                        "name": outcome.get("name"),
                        "price": outcome.get("price"),
                        "point": outcome.get("point"),
                    }
                )
            if key:
                book["markets"][key] = outcomes
        normalized_books.append(book)
    return normalized_books


def canonical_moneyline(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    books = snapshot.get("bookmakers", [])
    chosen = None
    for preferred in CANONICAL_BOOK_ORDER:
        chosen = next((book for book in books if book.get("key") == preferred and book.get("markets", {}).get("h2h")), None)
        if chosen:
            break
    if not chosen:
        chosen = next((book for book in books if book.get("markets", {}).get("h2h")), None)
    if not chosen:
        return {}

    outcomes = chosen.get("markets", {}).get("h2h", [])
    home_team = snapshot.get("home_team")
    away_team = snapshot.get("away_team")
    home = next((item for item in outcomes if item.get("name") == home_team), None)
    away = next((item for item in outcomes if item.get("name") == away_team), None)
    return {
        "book": chosen.get("title") or chosen.get("key"),
        "homeMoneyline": home.get("price") if home else None,
        "awayMoneyline": away.get("price") if away else None,
    }


def write_event_snapshot(event: Dict[str, Any], sport_label: str, pull_label: str, captured_at: str) -> None:
    commence_time = event.get("commence_time") or ""
    commence_date = commence_time[:10] if len(commence_time) >= 10 else captured_at[:10]
    game_id = f"{sport_label.lower()}-{event.get('id')}"
    home_team = event.get("home_team")
    away_team = event.get("away_team")
    matchup = f"{away_team} @ {home_team}" if away_team and home_team else event.get("id")
    bookmakers = extract_bookmakers(event)

    game_item = {
        "game_id": game_id,
        "provider_event_id": event.get("id"),
        "sport": sport_label,
        "sport_key": event.get("sport_key"),
        "sport_title": event.get("sport_title"),
        "commence_time": commence_time,
        "commence_date": commence_date,
        "home_team": home_team,
        "away_team": away_team,
        "matchup": matchup,
        "updated_at": captured_at,
        "dataStatus": "Collected",
    }
    snapshot_item = {
        "game_id": game_id,
        "captured_at": captured_at,
        "pull_label": pull_label,
        "sport": sport_label,
        "sport_key": event.get("sport_key"),
        "home_team": home_team,
        "away_team": away_team,
        "matchup": matchup,
        "commence_time": commence_time,
        "bookmakers": bookmakers,
        "raw_provider_payload": event,
    }

    GAMES_TABLE.put_item(Item=to_decimal_safe(game_item))
    SNAPSHOTS_TABLE.put_item(Item=to_decimal_safe(snapshot_item))


def ingest_odds(pull_label: str = "MANUAL", sports: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    requested = list(sports) if sports else list(SUPPORTED_SPORTS.keys())
    captured_at = utc_now()
    run_id = f"{captured_at}#{pull_label}#{uuid.uuid4().hex[:8]}"
    results = []
    total_events = 0
    errors = []

    for sport_label in requested:
        label = sport_label.upper()
        sport_key = SUPPORTED_SPORTS.get(label)
        if not sport_key:
            errors.append({"sport": sport_label, "error": "unsupported_sport"})
            continue
        try:
            events = fetch_odds_for_sport(sport_key)
            for event in events:
                write_event_snapshot(event, label, pull_label, captured_at)
            results.append({"sport": label, "sportKey": sport_key, "eventsStored": len(events)})
            total_events += len(events)
        except HTTPError as exc:
            errors.append({"sport": label, "error": f"provider_http_{exc.code}"})
        except (URLError, TimeoutError, RuntimeError) as exc:
            errors.append({"sport": label, "error": str(exc)})

    run_item = {
        "run_id": run_id,
        "captured_at": captured_at,
        "pull_label": pull_label,
        "sports": requested,
        "total_events": total_events,
        "results": results,
        "errors": errors,
        "status": "OK" if total_events and not errors else "PARTIAL" if total_events else "FAILED",
    }
    INGESTION_RUNS_TABLE.put_item(Item=to_decimal_safe(run_item))
    return run_item


def latest_snapshot_for_game(game_id: str) -> Optional[Dict[str, Any]]:
    result = SNAPSHOTS_TABLE.query(
        KeyConditionExpression=Key("game_id").eq(game_id),
        ScanIndexForward=False,
        Limit=1,
    )
    items = result.get("Items", [])
    return items[0] if items else None


def snapshots_for_game(game_id: str, limit: int = 96) -> List[Dict[str, Any]]:
    result = SNAPSHOTS_TABLE.query(
        KeyConditionExpression=Key("game_id").eq(game_id),
        ScanIndexForward=True,
        Limit=limit,
    )
    return result.get("Items", [])


def list_games(sport: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    today = datetime.now(timezone.utc).date().isoformat()
    if sport:
        result = GAMES_TABLE.query(
            IndexName="SportDateIndex",
            KeyConditionExpression=Key("sport").eq(sport.upper()) & Key("commence_date").gte(today),
            Limit=limit,
        )
    else:
        result = GAMES_TABLE.scan(Limit=limit)
    return sorted(result.get("Items", []), key=lambda item: item.get("commence_time", ""))


def build_game_response(game: Dict[str, Any]) -> Dict[str, Any]:
    snapshot = latest_snapshot_for_game(game["game_id"])
    moneyline = canonical_moneyline(snapshot or {}) if snapshot else {}
    home_ml = moneyline.get("homeMoneyline")
    away_ml = moneyline.get("awayMoneyline")
    favorite = game.get("home_team")
    underdog = game.get("away_team")
    favorite_ml = home_ml
    underdog_ml = away_ml
    if home_ml is not None and away_ml is not None and away_ml < home_ml:
        favorite = game.get("away_team")
        underdog = game.get("home_team")
        favorite_ml = away_ml
        underdog_ml = home_ml

    return {
        "id": game.get("game_id"),
        "league": game.get("sport"),
        "start": game.get("commence_time"),
        "matchup": game.get("matchup"),
        "favorite": favorite,
        "underdog": underdog,
        "favoriteMl": favorite_ml,
        "underdogMl": underdog_ml,
        "total": latest_total(snapshot),
        "movement": "Stored market data from provider snapshots",
        "confidence": "Pending model",
        "risk": "PENDING",
        "signals": ["DATA_COLLECTED"],
        "dataStatus": game.get("dataStatus", "Collected"),
        "sourceBook": moneyline.get("book"),
        "lastSnapshot": snapshot.get("captured_at") if snapshot else None,
    }


def latest_total(snapshot: Optional[Dict[str, Any]]) -> Optional[Any]:
    if not snapshot:
        return None
    for book in snapshot.get("bookmakers", []):
        totals = book.get("markets", {}).get("totals")
        if totals:
            first = totals[0]
            return first.get("point")
    return None


def build_line_movement(snapshots: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    for snapshot in snapshots:
        moneyline = canonical_moneyline(snapshot)
        rows.append(
            {
                "time": snapshot.get("captured_at"),
                "milestone": snapshot.get("pull_label"),
                "book": moneyline.get("book"),
                "homeTeam": snapshot.get("home_team"),
                "awayTeam": snapshot.get("away_team"),
                "homeMoneyline": moneyline.get("homeMoneyline"),
                "awayMoneyline": moneyline.get("awayMoneyline"),
                "total": latest_total(snapshot),
            }
        )
    return rows


def handle_http(event: Dict[str, Any]) -> Dict[str, Any]:
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    path = event.get("rawPath", "/")
    query = get_query(event)

    if method == "OPTIONS":
        return response(200, {"ok": True})

    if path == "/v1/health":
        return response(
            200,
            {
                "ok": True,
                "service": "silvers-syndicate-api",
                "time": utc_now(),
                "storage": "dynamodb",
                "provider": "odds-api",
                "supportedSports": list(SUPPORTED_SPORTS.keys()),
            },
        )

    if path == "/v1/ingest/odds" and method == "POST":
        body = parse_body(event)
        pull_label = body.get("pullLabel") or "MANUAL"
        sports = body.get("sports")
        return response(200, ingest_odds(pull_label=pull_label, sports=sports))

    if path == "/v1/slates/today":
        games = [build_game_response(game) for game in list_games(query.get("sport"))]
        if not games:
            return response(
                503,
                {
                    "games": [],
                    "rankings": [],
                    "source": "dynamodb",
                    "status": "NO_MARKET_DATA",
                    "message": "No stored odds snapshots found yet. Run the scheduled or manual odds ingestion first.",
                },
            )
        return response(
            200,
            {
                "games": games,
                "rankings": [],
                "source": "dynamodb",
                "status": "MARKET_DATA_ONLY",
                "message": "Real stored market data returned. Parlay model rankings are disabled until the full signal engine is enabled.",
            },
        )

    if path.endswith("/snapshots"):
        game_id = path.split("/")[3]
        snaps = snapshots_for_game(game_id)
        return response(200, {"gameId": game_id, "snapshots": snaps, "source": "dynamodb"})

    if path.endswith("/line-movement"):
        game_id = path.split("/")[3]
        snaps = snapshots_for_game(game_id)
        return response(
            200,
            {
                "gameId": game_id,
                "lineMovement": build_line_movement(snaps),
                "interval": "snapshot",
                "source": "dynamodb",
            },
        )

    if path == "/v1/parlays/build" and method == "POST":
        build_id = f"build-{uuid.uuid4().hex[:12]}"
        item = {
            "build_id": build_id,
            "created_at": utc_now(),
            "status": "ENGINE_NOT_READY",
            "reason": "Stored market data exists, but full parlay signal ranking is not enabled. No odds-only build was created.",
        }
        PARLAY_BUILDS_TABLE.put_item(Item=item)
        return response(422, {"buildId": build_id, "rankings": [], "source": "dynamodb", **item})

    if path.startswith("/v1/parlays/"):
        build_id = path.split("/")[-1]
        item = PARLAY_BUILDS_TABLE.get_item(Key={"build_id": build_id}).get("Item")
        if not item:
            return response(404, {"error": "build_not_found", "buildId": build_id})
        return response(200, item)

    return response(404, {"error": "Not found", "path": path})


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    if event.get("source") == "silvers.scheduler":
        return ingest_odds(pull_label=event.get("pullLabel", "SCHEDULED"))
    return handle_http(event)
