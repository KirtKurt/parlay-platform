import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set

import boto3
from boto3.dynamodb.conditions import Key


dynamodb = boto3.resource("dynamodb")
TABLE_NAME = os.environ.get("SNAPSHOTS_TABLE", "")
SPORTS = [
    "nba", "nfl", "mlb", "nhl", "wnba", "ncaam", "ncaaw", "cfb",
    "college_football_women", "college_baseball_men", "college_baseball_women",
    "college_softball_women", "soccer", "tennis",
]
CANONICAL_BOOKS = ["fanatics", "draftkings", "fanduel"]


def clean(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    if isinstance(value, list):
        return [clean(v) for v in value]
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    return value


def out(status: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json",
            "access-control-allow-origin": "*",
            "access-control-allow-methods": "GET,OPTIONS",
            "access-control-allow-headers": "content-type,authorization,x-inqsi-member-id",
        },
        "body": json.dumps(clean(body)),
    }


def table():
    if not TABLE_NAME:
        raise RuntimeError("SNAPSHOTS_TABLE is not configured")
    return dynamodb.Table(TABLE_NAME)


def query_params(event: Dict[str, Any]) -> Dict[str, str]:
    return event.get("queryStringParameters") or {}


def day_list(days: int) -> List[str]:
    days = max(1, min(int(days or 2), 7))
    today = datetime.now(timezone.utc).date()
    return [(today - timedelta(days=i)).isoformat() for i in range(days)]


def query_pull_records(sport: str, day: str) -> List[Dict[str, Any]]:
    result = table().query(KeyConditionExpression=Key("PK").eq(f"PULLS#{sport}#{day}"), ScanIndexForward=True, Limit=500)
    return result.get("Items") or []


def parse_dt(value: Any) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def minutes_since(value: Any) -> Optional[int]:
    dt = parse_dt(value)
    if dt is None:
        return None
    return int(max(0, (datetime.now(timezone.utc) - dt).total_seconds() // 60))


def summarize_sport(sport: str, days: List[str]) -> Dict[str, Any]:
    pulls: List[Dict[str, Any]] = []
    for day in days:
        pulls.extend(query_pull_records(sport, day))

    books: Set[str] = set()
    games_seen: Set[str] = set()
    latest_pull_at = ""
    total_games = 0

    for item in pulls:
        data = item.get("data") or {}
        pull_time = item.get("pulled_at") or data.get("pulled_at") or item.get("created_at") or ""
        if pull_time > latest_pull_at:
            latest_pull_at = pull_time
        for game in data.get("games") or []:
            game_id = str(game.get("game_id") or game.get("game_key") or "unknown")
            games_seen.add(game_id)
            total_games += 1
            for book in (game.get("books") or {}).keys():
                books.add(str(book).lower())

    missing_canonical = [book for book in CANONICAL_BOOKS if book not in books]
    latest_age = minutes_since(latest_pull_at) if latest_pull_at else None
    warnings = []
    if not pulls:
        warnings.append("NO_PULLS_FOUND")
    if pulls and len(pulls) < 2:
        warnings.append("LOW_PULL_DEPTH")
    if latest_age is None:
        warnings.append("NO_LATEST_PULL_TIME")
    elif latest_age > 90:
        warnings.append("STALE_PULL_HISTORY")
    if missing_canonical:
        warnings.append("MISSING_CANONICAL_BOOKS")

    status = "healthy"
    if "NO_PULLS_FOUND" in warnings:
        status = "missing"
    elif "STALE_PULL_HISTORY" in warnings or "MISSING_CANONICAL_BOOKS" in warnings:
        status = "warning"

    return {
        "sport": sport,
        "status": status,
        "pullCount": len(pulls),
        "uniqueGames": len(games_seen),
        "totalGameRows": total_games,
        "booksPresent": sorted(books),
        "missingCanonicalBooks": missing_canonical,
        "latestPullAt": latest_pull_at or None,
        "latestPullAgeMinutes": latest_age,
        "warnings": warnings,
    }


def handle(event: Dict[str, Any]) -> Dict[str, Any]:
    q = query_params(event)
    try:
        days = max(1, min(int(q.get("days") or 2), 7))
    except Exception:
        days = 2
    requested_sport = str(q.get("sport") or "").strip().lower()
    sports = [requested_sport] if requested_sport else SPORTS
    days_used = day_list(days)
    rows = [summarize_sport(sport, days_used) for sport in sports]

    total_pulls = sum(row["pullCount"] for row in rows)
    healthy = sum(1 for row in rows if row["status"] == "healthy")
    warning = sum(1 for row in rows if row["status"] == "warning")
    missing = sum(1 for row in rows if row["status"] == "missing")
    all_books = sorted({book for row in rows for book in row.get("booksPresent", [])})
    warnings = sorted({warning for row in rows for warning in row.get("warnings", [])})

    return out(200, {
        "ok": True,
        "dashboard": "market_data_quality",
        "windowDays": days,
        "datesChecked": days_used,
        "summary": {
            "sportsChecked": len(rows),
            "healthySports": healthy,
            "warningSports": warning,
            "missingSports": missing,
            "totalPulls": total_pulls,
            "booksObserved": all_books,
            "globalWarnings": warnings,
        },
        "canonicalBooksRequired": CANONICAL_BOOKS,
        "sports": rows,
        "notes": [
            "This dashboard reads immutable pull-history records only.",
            "It does not infer missing books or fabricate pull data.",
        ],
    })


def route(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    event = event or {}
    method = (event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method") or "GET").upper()
    path = (event.get("rawPath") or event.get("path") or "/").rstrip("/") or "/"
    if method == "OPTIONS" and (path.startswith("/v1/inqsi/admin/analytics") or path.startswith("/v1/admin/analytics")):
        return out(200, {"ok": True})
    if path in {"/v1/inqsi/admin/analytics/data-quality", "/v1/admin/analytics/data-quality"} and method == "GET":
        return handle(event)
    return None
