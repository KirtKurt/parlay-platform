import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

import boto3
from boto3.dynamodb.conditions import Attr, Key


dynamodb = boto3.resource("dynamodb")
TABLE_NAME = os.environ.get("SNAPSHOTS_TABLE", "")
SPORTS = ["nba", "nfl", "mlb", "nhl", "wnba", "ncaam", "ncaaw", "cfb", "soccer", "tennis"]
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


def today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def yday() -> str:
    return (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()


def parse_dt(value: Any) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def minutes_since(value: Any) -> Optional[int]:
    dt = parse_dt(value)
    if not dt:
        return None
    return int(max(0, (datetime.now(timezone.utc) - dt).total_seconds() // 60))


def alert(severity: str, code: str, message: str, source: str, details: Dict[str, Any]) -> Dict[str, Any]:
    return {"severity": severity, "code": code, "message": message, "source": source, "details": details}


def query_pulls(sport: str, date_key: str) -> List[Dict[str, Any]]:
    try:
        res = table().query(KeyConditionExpression=Key("PK").eq(f"PULLS#{sport}#{date_key}"), Limit=500)
        return res.get("Items") or []
    except Exception:
        return []


def scan_filtered(expression, limit: int = 500) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    start_key = None
    while True:
        args = {"FilterExpression": expression, "Limit": min(250, max(1, limit - len(items)))}
        if start_key:
            args["ExclusiveStartKey"] = start_key
        res = table().scan(**args)
        items.extend(res.get("Items") or [])
        if len(items) >= limit:
            return items[:limit]
        start_key = res.get("LastEvaluatedKey")
        if not start_key:
            return items


def market_alerts() -> List[Dict[str, Any]]:
    rows = []
    for sport in SPORTS:
        pulls = query_pulls(sport, today()) or query_pulls(sport, yday())
        if not pulls:
            rows.append(alert("HIGH", "NO_PULL_HISTORY", f"No pull history found for {sport}.", "market_data", {"sport": sport}))
            continue
        latest_time = ""
        books = set()
        for item in pulls:
            data = item.get("data") or {}
            pulled = item.get("pulled_at") or data.get("pulled_at") or item.get("created_at") or ""
            if pulled > latest_time:
                latest_time = pulled
            for game in data.get("games") or []:
                books.update(str(book).lower() for book in (game.get("books") or {}).keys())
        age = minutes_since(latest_time)
        if age is not None and age > 90:
            rows.append(alert("MEDIUM", "STALE_PULL_HISTORY", f"Latest {sport} pull is older than 90 minutes.", "market_data", {"sport": sport, "latestPullAt": latest_time, "ageMinutes": age}))
        missing = [book for book in CANONICAL_BOOKS if book not in books]
        if missing:
            rows.append(alert("MEDIUM", "MISSING_CANONICAL_BOOKS", f"{sport} is missing canonical books.", "market_data", {"sport": sport, "missingBooks": missing, "booksPresent": sorted(books)}))
    return rows


def moderation_alerts() -> List[Dict[str, Any]]:
    expr = Attr("record_type").eq("member_image_upload") | Attr("recordType").eq("member_image_upload")
    uploads = scan_filtered(expr, 1000)
    pending = [u for u in uploads if (u.get("moderation_status") or u.get("moderationStatus")) in {"manual_review", "queued_for_scan", "pending"}]
    rows = []
    if len(pending) >= 10:
        rows.append(alert("MEDIUM", "MODERATION_QUEUE_BACKLOG", "Moderation queue has 10 or more pending items.", "moderation", {"pendingCount": len(pending)}))
    if len(pending) >= 25:
        rows.append(alert("HIGH", "MODERATION_QUEUE_HIGH_BACKLOG", "Moderation queue has 25 or more pending items.", "moderation", {"pendingCount": len(pending)}))
    return rows


def integrity_alerts() -> List[Dict[str, Any]]:
    try:
        res = table().query(KeyConditionExpression=Key("PK").eq(f"INTEGRITY#ATTEMPT#{today()}"), Limit=500)
        attempts = res.get("Items") or []
    except Exception:
        attempts = []
    under18 = [a for a in attempts if a.get("accountQualityStatus") == "BLOCKED_UNDER_18"]
    highrisk = [a for a in attempts if a.get("riskStatus") == "HIGH_RISK"]
    rows = []
    if len(under18) > 0:
        rows.append(alert("HIGH", "UNDER_18_SIGNUP_ATTEMPTS", "Under-18 signup attempts were blocked today.", "account_integrity", {"count": len(under18)}))
    if len(highrisk) >= 5:
        rows.append(alert("MEDIUM", "HIGH_RISK_SIGNUP_VOLUME", "Five or more high-risk signup attempts today.", "account_integrity", {"count": len(highrisk)}))
    return rows


def creator_alerts() -> List[Dict[str, Any]]:
    expr = Attr("recordType").eq("creator_application")
    apps = scan_filtered(expr, 500)
    pending = [a for a in apps if a.get("status") in {"SUBMITTED", "PENDING", "MANUAL_REVIEW"}]
    if len(pending) >= 5:
        return [alert("LOW", "PENDING_CREATOR_APPLICATIONS", "Creator applications are waiting for review.", "creator_program", {"pendingApplications": len(pending)})]
    return []


def analytics_alerts() -> List[Dict[str, Any]]:
    try:
        res = table().query(KeyConditionExpression=Key("PK").eq(f"ANALYTICS#{today()}"), Limit=500)
        events = res.get("Items") or []
    except Exception:
        events = []
    rows = []
    if not events:
        rows.append(alert("MEDIUM", "NO_ANALYTICS_EVENTS_TODAY", "No analytics events have been stored today.", "analytics", {}))
    outcomes = [e for e in events if isinstance(e.get("metadata"), dict) and any(k in e.get("metadata", {}) for k in ["modelHit", "model_hit", "top3Contained", "top_3_contained"])]
    scans = [e for e in events if e.get("eventType") == "slip_scanned"]
    if scans and not outcomes:
        rows.append(alert("LOW", "NO_ALGORITHM_OUTCOMES_LOGGED", "Slip scans exist but no algorithm outcomes are logged yet.", "algorithm_analytics", {"slipScans": len(scans)}))
    return rows


def handle(event: Dict[str, Any]) -> Dict[str, Any]:
    alerts = []
    alerts.extend(market_alerts())
    alerts.extend(moderation_alerts())
    alerts.extend(integrity_alerts())
    alerts.extend(creator_alerts())
    alerts.extend(analytics_alerts())
    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    alerts.sort(key=lambda row: (order.get(row.get("severity"), 9), row.get("code", "")))
    return out(200, {
        "ok": True,
        "dashboard": "admin_alerts",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "totalAlerts": len(alerts),
            "high": sum(1 for a in alerts if a.get("severity") == "HIGH"),
            "medium": sum(1 for a in alerts if a.get("severity") == "MEDIUM"),
            "low": sum(1 for a in alerts if a.get("severity") == "LOW"),
        },
        "alerts": alerts,
        "notes": ["Read-only alert generation. No actions are executed automatically."],
    })


def route(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    event = event or {}
    method = (event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method") or "GET").upper()
    path = (event.get("rawPath") or event.get("path") or "/").rstrip("/") or "/"
    if method == "OPTIONS" and (path.startswith("/v1/inqsi/admin/analytics") or path.startswith("/v1/admin/analytics")):
        return out(200, {"ok": True})
    if path in {"/v1/inqsi/admin/analytics/alerts", "/v1/admin/analytics/alerts"} and method == "GET":
        return handle(event)
    return None
