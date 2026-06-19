import json
import os
import uuid
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Tuple
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
REQUIRED_STATUS_SNAPSHOTS = 3
REQUIRED_STATUS_BOOKS = 2

DDB = boto3.resource("dynamodb")
GAMES_TABLE = DDB.Table(os.environ["GAMES_TABLE"])
SNAPSHOTS_TABLE = DDB.Table(os.environ["SNAPSHOTS_TABLE"])
GAME_STATUS_TABLE = DDB.Table(os.environ["GAME_STATUS_TABLE"])
PARLAY_BUILDS_TABLE = DDB.Table(os.environ["PARLAY_BUILDS_TABLE"])
AUDIT_EVENTS_TABLE = DDB.Table(os.environ["AUDIT_EVENTS_TABLE"])
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
    return {"statusCode": status_code, "headers": CORS_HEADERS, "body": json.dumps(as_json_safe(body))}


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
        book = {"key": bookmaker.get("key"), "title": bookmaker.get("title"), "last_update": bookmaker.get("last_update"), "markets": {}}
        for market in bookmaker.get("markets", []):
            key = market.get("key")
            outcomes = [{"name": o.get("name"), "price": o.get("price"), "point": o.get("point")} for o in market.get("outcomes", [])]
            if key:
                book["markets"][key] = outcomes
        normalized_books.append(book)
    return normalized_books


def american_to_implied(price: Optional[int]) -> Optional[float]:
    if price is None:
        return None
    try:
        price = int(price)
    except (TypeError, ValueError):
        return None
    if price < 0:
        return abs(price) / (abs(price) + 100)
    return 100 / (price + 100)


def fair_probabilities(home_price: Optional[int], away_price: Optional[int]) -> Tuple[Optional[float], Optional[float]]:
    home_raw = american_to_implied(home_price)
    away_raw = american_to_implied(away_price)
    if home_raw is None or away_raw is None:
        return None, None
    total = home_raw + away_raw
    if total <= 0:
        return None, None
    return home_raw / total, away_raw / total


def book_h2h(snapshot: Dict[str, Any], book_key: str) -> Optional[Dict[str, Any]]:
    home_team = snapshot.get("home_team")
    away_team = snapshot.get("away_team")
    for book in snapshot.get("bookmakers", []):
        if book.get("key") != book_key:
            continue
        outcomes = book.get("markets", {}).get("h2h", [])
        home = next((item for item in outcomes if item.get("name") == home_team), None)
        away = next((item for item in outcomes if item.get("name") == away_team), None)
        if home and away:
            home_fair, away_fair = fair_probabilities(home.get("price"), away.get("price"))
            return {
                "book": book.get("title") or book.get("key"),
                "bookKey": book.get("key"),
                "homeMoneyline": home.get("price"),
                "awayMoneyline": away.get("price"),
                "homeFairProb": home_fair,
                "awayFairProb": away_fair,
            }
    return None


def canonical_moneyline(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    for preferred in CANONICAL_BOOK_ORDER:
        h2h = book_h2h(snapshot, preferred)
        if h2h:
            return h2h
    for book in snapshot.get("bookmakers", []):
        h2h = book_h2h(snapshot, book.get("key"))
        if h2h:
            return h2h
    return {}


def latest_total(snapshot: Optional[Dict[str, Any]]) -> Optional[Any]:
    if not snapshot:
        return None
    for book in snapshot.get("bookmakers", []):
        totals = book.get("markets", {}).get("totals")
        if totals:
            return totals[0].get("point")
    return None


def latest_spread(snapshot: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not snapshot:
        return None
    for preferred in CANONICAL_BOOK_ORDER:
        for book in snapshot.get("bookmakers", []):
            if book.get("key") != preferred:
                continue
            spreads = book.get("markets", {}).get("spreads")
            if spreads:
                return {"book": book.get("title") or book.get("key"), "outcomes": spreads}
    return None


def snapshots_for_game(game_id: str, limit: int = 192) -> List[Dict[str, Any]]:
    result = SNAPSHOTS_TABLE.query(KeyConditionExpression=Key("game_id").eq(game_id), ScanIndexForward=True, Limit=limit)
    return result.get("Items", [])


def latest_snapshot_for_game(game_id: str) -> Optional[Dict[str, Any]]:
    result = SNAPSHOTS_TABLE.query(KeyConditionExpression=Key("game_id").eq(game_id), ScanIndexForward=False, Limit=1)
    items = result.get("Items", [])
    return items[0] if items else None


def latest_status_for_game(game_id: str) -> Optional[Dict[str, Any]]:
    result = GAME_STATUS_TABLE.query(KeyConditionExpression=Key("game_id").eq(game_id), ScanIndexForward=False, Limit=1)
    items = result.get("Items", [])
    return items[0] if items else None


def status_history_for_game(game_id: str, limit: int = 192) -> List[Dict[str, Any]]:
    result = GAME_STATUS_TABLE.query(KeyConditionExpression=Key("game_id").eq(game_id), ScanIndexForward=True, Limit=limit)
    return result.get("Items", [])


def compute_market_status(game: Dict[str, Any], snapshots: List[Dict[str, Any]], captured_at: str) -> Dict[str, Any]:
    game_id = game["game_id"]
    sport = game.get("sport")
    home_team = game.get("home_team")
    away_team = game.get("away_team")
    base = snapshots[0] if snapshots else None
    current = snapshots[-1] if snapshots else None
    previous = snapshots[-2] if len(snapshots) >= 2 else None

    if len(snapshots) < REQUIRED_STATUS_SNAPSHOTS:
        return {
            "game_id": game_id,
            "status_captured_at": captured_at,
            "sport": sport,
            "status": "DATA_INCOMPLETE",
            "marketLean": "No clear edge",
            "confidenceLow": 0,
            "confidenceHigh": 0,
            "riskLevel": "INCOMPLETE",
            "primarySignal": "DATA_INCOMPLETE",
            "secondarySignals": [],
            "bookAgreement": 0,
            "recommendation": "DATA_INCOMPLETE",
            "eligibleAnchor": False,
            "eligibleCoinFlip": False,
            "doNotUse": True,
            "reasonCodes": [f"Need at least {REQUIRED_STATUS_SNAPSHOTS} snapshots"],
            "snapshotsAnalyzed": len(snapshots),
        }

    book_moves = []
    magnitude_values = []
    for book_key in CANONICAL_BOOK_ORDER:
        base_h2h = book_h2h(base, book_key) if base else None
        curr_h2h = book_h2h(current, book_key) if current else None
        prev_h2h = book_h2h(previous, book_key) if previous else None
        if not base_h2h or not curr_h2h:
            continue
        home_delta = (curr_h2h.get("homeFairProb") or 0) - (base_h2h.get("homeFairProb") or 0)
        away_delta = (curr_h2h.get("awayFairProb") or 0) - (base_h2h.get("awayFairProb") or 0)
        last_home_delta = None
        if prev_h2h:
            last_home_delta = (curr_h2h.get("homeFairProb") or 0) - (prev_h2h.get("homeFairProb") or 0)
        magnitude = max(abs(home_delta), abs(away_delta))
        magnitude_values.append(magnitude)
        if abs(home_delta) < 0.006 and abs(away_delta) < 0.006:
            direction = "FLAT"
            lean = None
        elif home_delta > away_delta:
            direction = "HOME"
            lean = home_team
        else:
            direction = "AWAY"
            lean = away_team
        book_moves.append({"bookKey": book_key, "book": curr_h2h.get("book"), "direction": direction, "lean": lean, "magnitude": magnitude, "lastHomeDelta": last_home_delta})

    if len(book_moves) < REQUIRED_STATUS_BOOKS:
        status = "DATA_INCOMPLETE"
        market_lean = "No clear edge"
        confidence_low = 0
        confidence_high = 0
        risk = "INCOMPLETE"
        recommendation = "DATA_INCOMPLETE"
        primary_signal = "DATA_INCOMPLETE"
        reason_codes = [f"Need at least {REQUIRED_STATUS_BOOKS} books with h2h data"]
        agreement = len(book_moves)
        secondary = []
    else:
        lean_votes = [move["lean"] for move in book_moves if move.get("lean")]
        vote_counts = Counter(lean_votes)
        market_lean, agreement = (vote_counts.most_common(1)[0] if vote_counts else ("No clear edge", 0))
        avg_magnitude = sum(magnitude_values) / max(len(magnitude_values), 1)
        max_magnitude = max(magnitude_values) if magnitude_values else 0
        disagreement = len({move["lean"] for move in book_moves if move.get("lean")}) > 1
        one_book_spike = max_magnitude >= 0.035 and sum(1 for val in magnitude_values if val >= 0.015) == 1
        reversal = False
        if previous:
            previous_votes = []
            for book_key in CANONICAL_BOOK_ORDER:
                base_h2h = book_h2h(base, book_key)
                prev_h2h = book_h2h(previous, book_key)
                if not base_h2h or not prev_h2h:
                    continue
                home_delta = (prev_h2h.get("homeFairProb") or 0) - (base_h2h.get("homeFairProb") or 0)
                if abs(home_delta) >= 0.006:
                    previous_votes.append(home_team if home_delta > 0 else away_team)
            if previous_votes and market_lean != "No clear edge":
                prior_lean = Counter(previous_votes).most_common(1)[0][0]
                reversal = prior_lean != market_lean and agreement >= 2

        secondary = []
        if disagreement:
            secondary.append("BOOK_DIVERGENCE")
        if one_book_spike:
            secondary.append("ONE_BOOK_SPIKE")
        if reversal:
            secondary.append("REVERSAL_WATCH")

        if one_book_spike or (disagreement and max_magnitude >= 0.02):
            status = "MARKET_ANOMALY"
            primary_signal = "MARKET_ANOMALY"
            risk = "HIGH"
            recommendation = "WATCH_ONLY"
            reason_codes = ["Book movement is not aligned enough to trust as a clean signal"]
        elif reversal:
            status = "REVERSAL_WATCH"
            primary_signal = "REVERSAL"
            risk = "HIGH"
            recommendation = "WATCH_ONLY"
            reason_codes = ["Market direction changed after earlier movement"]
        elif disagreement:
            status = "COIN_FLIP"
            primary_signal = "COIN_FLIP"
            risk = "HIGH"
            recommendation = "COIN_FLIP_CANDIDATE"
            reason_codes = ["Books disagree on the live market direction"]
        elif agreement >= 2 and avg_magnitude >= 0.015:
            status = "STEAMING"
            primary_signal = "STEAM"
            risk = "LOW" if agreement == 3 and avg_magnitude >= 0.025 else "MODERATE"
            recommendation = "USE_AS_ANCHOR" if risk != "HIGH" else "WATCH_ONLY"
            reason_codes = ["Sustained movement from baseline with multi-book confirmation"]
        elif agreement >= 2 and avg_magnitude >= 0.006:
            status = "HOLDING"
            primary_signal = "MOMENTUM"
            risk = "MODERATE"
            recommendation = "WATCH_ONLY"
            reason_codes = ["Market has a lean but movement is not strong enough for anchor status"]
        else:
            status = "NO_CLEAR_EDGE"
            primary_signal = "NO_CLEAR_EDGE"
            risk = "HIGH"
            recommendation = "AVOID"
            reason_codes = ["Movement is too flat or too compressed"]

        base_conf = 50 + min(18, agreement * 4 + avg_magnitude * 260)
        if risk == "HIGH":
            base_conf -= 6
        confidence_low = max(0, int(round(base_conf - 3)))
        confidence_high = min(100, int(round(base_conf + 3)))

    return {
        "game_id": game_id,
        "status_captured_at": captured_at,
        "sport": sport,
        "matchup": game.get("matchup"),
        "home_team": home_team,
        "away_team": away_team,
        "commence_time": game.get("commence_time"),
        "status": status,
        "marketLean": market_lean,
        "confidenceLow": confidence_low,
        "confidenceHigh": confidence_high,
        "riskLevel": risk,
        "primarySignal": primary_signal,
        "secondarySignals": secondary,
        "bookAgreement": agreement,
        "bookMoves": book_moves,
        "recommendation": recommendation,
        "eligibleAnchor": recommendation == "USE_AS_ANCHOR",
        "eligibleCoinFlip": recommendation == "COIN_FLIP_CANDIDATE",
        "doNotUse": recommendation in ["AVOID", "DATA_INCOMPLETE"],
        "reasonCodes": reason_codes,
        "snapshotsAnalyzed": len(snapshots),
        "lastSnapshot": current.get("captured_at") if current else None,
    }


def put_audit_event(entity_id: str, event_type: str, payload: Dict[str, Any], created_at: Optional[str] = None) -> Dict[str, Any]:
    created_at = created_at or utc_now()
    item = {
        "audit_id": f"{event_type}#{created_at}#{uuid.uuid4().hex[:10]}",
        "entity_id": entity_id,
        "event_type": event_type,
        "created_at": created_at,
        "payload": payload,
    }
    AUDIT_EVENTS_TABLE.put_item(Item=to_decimal_safe(item))
    return item


def refresh_status_for_game(game_id: str, captured_at: str) -> Optional[Dict[str, Any]]:
    game = GAMES_TABLE.get_item(Key={"game_id": game_id}).get("Item")
    if not game:
        return None
    snaps = snapshots_for_game(game_id)
    status = compute_market_status(game, snaps, captured_at)
    GAME_STATUS_TABLE.put_item(Item=to_decimal_safe(status))
    GAMES_TABLE.update_item(
        Key={"game_id": game_id},
        UpdateExpression="SET latest_status = :s, latest_market_lean = :l, latest_risk = :r, latest_recommendation = :rec, latest_status_at = :t",
        ExpressionAttributeValues={
            ":s": status.get("status"),
            ":l": status.get("marketLean"),
            ":r": status.get("riskLevel"),
            ":rec": status.get("recommendation"),
            ":t": captured_at,
        },
    )
    put_audit_event(game_id, "GAME_STATUS", status, created_at=captured_at)
    return status


def write_event_snapshot(event: Dict[str, Any], sport_label: str, pull_label: str, captured_at: str) -> str:
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
    return game_id


def ingest_odds(pull_label: str = "MANUAL", sports: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    requested = list(sports) if sports else list(SUPPORTED_SPORTS.keys())
    captured_at = utc_now()
    run_id = f"{captured_at}#{pull_label}#{uuid.uuid4().hex[:8]}"
    results = []
    total_events = 0
    status_updates = 0
    errors = []
    stored_game_ids: List[str] = []

    for sport_label in requested:
        label = sport_label.upper()
        sport_key = SUPPORTED_SPORTS.get(label)
        if not sport_key:
            errors.append({"sport": sport_label, "error": "unsupported_sport"})
            continue
        try:
            events = fetch_odds_for_sport(sport_key)
            for event in events:
                stored_game_ids.append(write_event_snapshot(event, label, pull_label, captured_at))
            results.append({"sport": label, "sportKey": sport_key, "eventsStored": len(events)})
            total_events += len(events)
        except HTTPError as exc:
            errors.append({"sport": label, "error": f"provider_http_{exc.code}"})
        except (URLError, TimeoutError, RuntimeError) as exc:
            errors.append({"sport": label, "error": str(exc)})

    for game_id in sorted(set(stored_game_ids)):
        try:
            if refresh_status_for_game(game_id, captured_at):
                status_updates += 1
        except Exception as exc:
            errors.append({"gameId": game_id, "error": f"status_engine_failed: {exc}"})

    run_item = {
        "run_id": run_id,
        "captured_at": captured_at,
        "pull_label": pull_label,
        "sports": requested,
        "total_events": total_events,
        "status_updates": status_updates,
        "results": results,
        "errors": errors,
        "status": "OK" if total_events and not errors else "PARTIAL" if total_events else "FAILED",
    }
    INGESTION_RUNS_TABLE.put_item(Item=to_decimal_safe(run_item))
    put_audit_event(run_id, "INGESTION_RUN", run_item, created_at=captured_at)
    return run_item


def list_games(sport: Optional[str] = None, limit: int = 80) -> List[Dict[str, Any]]:
    today = datetime.now(timezone.utc).date().isoformat()
    if sport:
        result = GAMES_TABLE.query(IndexName="SportDateIndex", KeyConditionExpression=Key("sport").eq(sport.upper()) & Key("commence_date").gte(today), Limit=limit)
    else:
        result = GAMES_TABLE.scan(Limit=limit)
    return sorted(result.get("Items", []), key=lambda item: item.get("commence_time", ""))


def build_game_response(game: Dict[str, Any]) -> Dict[str, Any]:
    snapshot = latest_snapshot_for_game(game["game_id"])
    status = latest_status_for_game(game["game_id"])
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
        "spread": latest_spread(snapshot),
        "movement": "15-minute market heartbeat stored in DynamoDB",
        "confidence": f"{status.get('confidenceLow')}–{status.get('confidenceHigh')}%" if status else "Pending",
        "risk": status.get("riskLevel") if status else "PENDING",
        "signals": [status.get("primarySignal")] + status.get("secondarySignals", []) if status else ["DATA_COLLECTED"],
        "status": status.get("status") if status else "PENDING",
        "marketLean": status.get("marketLean") if status else "Pending",
        "recommendation": status.get("recommendation") if status else "WATCH_ONLY",
        "reasonCodes": status.get("reasonCodes", []) if status else [],
        "dataStatus": game.get("dataStatus", "Collected"),
        "sourceBook": moneyline.get("book"),
        "lastSnapshot": snapshot.get("captured_at") if snapshot else None,
        "lastStatus": status.get("status_captured_at") if status else None,
    }


def build_line_movement(snapshots: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    for snapshot in snapshots:
        moneyline = canonical_moneyline(snapshot)
        rows.append({
            "time": snapshot.get("captured_at"),
            "milestone": snapshot.get("pull_label"),
            "book": moneyline.get("book"),
            "homeTeam": snapshot.get("home_team"),
            "awayTeam": snapshot.get("away_team"),
            "homeMoneyline": moneyline.get("homeMoneyline"),
            "awayMoneyline": moneyline.get("awayMoneyline"),
            "homeFairProb": moneyline.get("homeFairProb"),
            "awayFairProb": moneyline.get("awayFairProb"),
            "total": latest_total(snapshot),
        })
    return rows


def audit_events_for_entity(entity_id: str, limit: int = 200) -> List[Dict[str, Any]]:
    result = AUDIT_EVENTS_TABLE.query(IndexName="EntityAuditIndex", KeyConditionExpression=Key("entity_id").eq(entity_id), ScanIndexForward=True, Limit=limit)
    return result.get("Items", [])


def handle_http(event: Dict[str, Any]) -> Dict[str, Any]:
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    path = event.get("rawPath", "/")
    query = get_query(event)
    if method == "OPTIONS":
        return response(200, {"ok": True})
    if path == "/v1/health":
        return response(200, {"ok": True, "service": "silvers-syndicate-api", "time": utc_now(), "storage": "dynamodb", "provider": "odds-api", "snapshotCadence": "15 minutes", "auditStorage": True, "supportedSports": list(SUPPORTED_SPORTS.keys())})
    if path == "/v1/ingest/odds" and method == "POST":
        body = parse_body(event)
        return response(200, ingest_odds(pull_label=body.get("pullLabel") or "MANUAL", sports=body.get("sports")))
    if path == "/v1/slates/today":
        games = [build_game_response(game) for game in list_games(query.get("sport"))]
        if not games:
            return response(503, {"games": [], "rankings": [], "source": "dynamodb", "status": "NO_MARKET_DATA", "message": "No stored odds snapshots found yet. Run the scheduled or manual odds ingestion first."})
        return response(200, {"games": games, "rankings": [], "source": "dynamodb", "status": "MARKET_STATUS_ACTIVE", "message": "Real stored market data returned with 15-minute status engine outputs."})
    if path.endswith("/snapshots"):
        game_id = path.split("/")[3]
        return response(200, {"gameId": game_id, "snapshots": snapshots_for_game(game_id), "source": "dynamodb"})
    if path.endswith("/line-movement"):
        game_id = path.split("/")[3]
        snaps = snapshots_for_game(game_id)
        return response(200, {"gameId": game_id, "lineMovement": build_line_movement(snaps), "interval": "15-minute snapshots", "source": "dynamodb"})
    if path.endswith("/status"):
        game_id = path.split("/")[3]
        return response(200, {"gameId": game_id, "current": latest_status_for_game(game_id), "history": status_history_for_game(game_id), "source": "dynamodb"})
    if path == "/v1/audit/games":
        game_id = query.get("gameId") or query.get("game_id")
        if not game_id:
            return response(400, {"error": "gameId is required"})
        return response(200, {"gameId": game_id, "statusHistory": status_history_for_game(game_id), "auditEvents": audit_events_for_entity(game_id)})
    if path == "/v1/audit/games/result" and method == "POST":
        body = parse_body(event)
        game_id = body.get("gameId") or body.get("game_id")
        if not game_id:
            return response(400, {"error": "gameId is required"})
        current = latest_status_for_game(game_id) or {}
        winner = body.get("winner")
        lean = current.get("marketLean")
        correct = bool(winner and lean and winner.lower() in str(lean).lower())
        audit = put_audit_event(game_id, "GAME_RESULT", {"result": body, "prediction": current, "correct": correct})
        return response(200, {"stored": True, "correct": correct, "audit": audit})
    if path == "/v1/parlays/build" and method == "POST":
        body = parse_body(event)
        build_id = f"build-{uuid.uuid4().hex[:12]}"
        item = {"build_id": build_id, "created_at": utc_now(), "status": "ENGINE_NOT_READY", "requestedLegs": body.get("legs", []), "reason": "Game-level market status is active, but full 3-leg parlay ranking is not enabled. No odds-only build was created."}
        PARLAY_BUILDS_TABLE.put_item(Item=to_decimal_safe(item))
        put_audit_event(build_id, "PARLAY_BUILD", item, created_at=item["created_at"])
        return response(422, {"buildId": build_id, "rankings": [], "source": "dynamodb", **item})
    if path.startswith("/v1/parlays/"):
        build_id = path.split("/")[-1]
        item = PARLAY_BUILDS_TABLE.get_item(Key={"build_id": build_id}).get("Item")
        if not item:
            return response(404, {"error": "build_not_found", "buildId": build_id})
        return response(200, item)
    if path == "/v1/audit/parlays":
        build_id = query.get("buildId") or query.get("build_id")
        if not build_id:
            return response(400, {"error": "buildId is required"})
        return response(200, {"buildId": build_id, "auditEvents": audit_events_for_entity(build_id)})
    if path == "/v1/audit/parlays/result" and method == "POST":
        body = parse_body(event)
        build_id = body.get("buildId") or body.get("build_id")
        if not build_id:
            return response(400, {"error": "buildId is required"})
        audit = put_audit_event(build_id, "PARLAY_RESULT", {"result": body})
        return response(200, {"stored": True, "audit": audit})
    return response(404, {"error": "Not found", "path": path})


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    if event.get("source") == "silvers.scheduler":
        return ingest_odds(pull_label=event.get("pullLabel", "SCHEDULED"))
    return handle_http(event)
