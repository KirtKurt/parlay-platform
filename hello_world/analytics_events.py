import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

import boto3
from boto3.dynamodb.conditions import Key


dynamodb = boto3.resource("dynamodb")
TABLE_NAME = os.environ.get("SNAPSHOTS_TABLE", "")


ALLOWED_EVENT_TYPES = {
    "member_registered",
    "member_logged_in",
    "sport_selected",
    "games_selected",
    "slip_scanned",
    "parlay_built",
    "parlay_refused",
    "public_slip_created",
    "image_uploaded",
    "creator_page_viewed",
    "subscription_started",
    "subscription_cancelled",
    "analytics_smoke_test",
}


ACTIVITY_EVENT_TYPES = {
    "member_registered",
    "member_logged_in",
    "sport_selected",
    "games_selected",
    "slip_scanned",
    "parlay_built",
    "parlay_refused",
    "public_slip_created",
    "image_uploaded",
    "creator_page_viewed",
}


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def date_key(ts: str) -> str:
    return ts[:10]


def today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def date_range(days: int) -> List[str]:
    days = max(1, min(int(days or 1), 14))
    today = datetime.now(timezone.utc).date()
    return [(today - timedelta(days=offset)).isoformat() for offset in range(days)]


def safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    if isinstance(value, list):
        return [safe(v) for v in value]
    if isinstance(value, dict):
        return {k: safe(v) for k, v in value.items()}
    return value


def resp(status: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json",
            "access-control-allow-origin": "*",
            "access-control-allow-methods": "GET,POST,OPTIONS",
            "access-control-allow-headers": "content-type,authorization,x-inqsi-member-id,x-inqsi-session-id",
        },
        "body": json.dumps(safe(body)),
    }


def parse_body(event: Dict[str, Any]) -> Dict[str, Any]:
    try:
        payload = json.loads(event.get("body") or "{}")
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def headers(event: Dict[str, Any]) -> Dict[str, str]:
    return {str(k).lower(): str(v) for k, v in (event.get("headers") or {}).items() if v is not None}


def query_params(event: Dict[str, Any]) -> Dict[str, str]:
    return event.get("queryStringParameters") or {}


def table():
    if not TABLE_NAME:
        raise RuntimeError("SNAPSHOTS_TABLE is not configured")
    return dynamodb.Table(TABLE_NAME)


def client_ip(event: Dict[str, Any]) -> Optional[str]:
    rc = event.get("requestContext") or {}
    http = rc.get("http") or {}
    return http.get("sourceIp") or rc.get("identity", {}).get("sourceIp")


def handle_health() -> Dict[str, Any]:
    return resp(200, {
        "ok": True,
        "service": "inqsi-analytics-events",
        "version": "v1",
        "tableConfigured": bool(TABLE_NAME),
        "collectorReady": bool(TABLE_NAME),
        "memberActivityDashboardReady": True,
        "supportedEventTypes": sorted(ALLOWED_EVENT_TYPES),
        "liveEndpoints": [
            "/v1/inqsi/analytics/health",
            "/v1/inqsi/analytics/event",
            "/v1/inqsi/admin/analytics/members",
        ],
        "nextBuilds": [
            "subscription_funnel_dashboard",
            "algorithm_performance_dashboard",
            "market_data_quality_dashboard",
            "moderation_analytics_dashboard",
            "creator_analytics_dashboard",
            "admin_alert_system",
        ],
    })


def handle_event(event: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
    event_type = str(data.get("eventType") or data.get("event_type") or "").strip()
    if not event_type:
        return resp(400, {"ok": False, "error": "eventType_required"})
    if event_type not in ALLOWED_EVENT_TYPES:
        return resp(400, {"ok": False, "error": "unsupported_event_type", "eventType": event_type, "supportedEventTypes": sorted(ALLOWED_EVENT_TYPES)})

    h = headers(event)
    created_at = utc_now()
    event_id = str(data.get("eventId") or data.get("event_id") or f"evt_{uuid.uuid4().hex[:18]}")
    member_id = data.get("memberId") or data.get("member_id") or h.get("x-inqsi-member-id") or h.get("x-member-id")
    session_id = data.get("sessionId") or data.get("session_id") or h.get("x-inqsi-session-id")
    anonymous_id = data.get("anonymousId") or data.get("anonymous_id")
    metadata = data.get("metadata") or {}
    if not isinstance(metadata, dict):
        return resp(400, {"ok": False, "error": "metadata_must_be_object"})

    item = {
        "PK": f"ANALYTICS#{date_key(created_at)}",
        "SK": f"EVENT#{created_at}#{event_id}",
        "recordType": "analytics_event",
        "eventId": event_id,
        "eventType": event_type,
        "memberId": member_id or "",
        "anonymousId": anonymous_id or "",
        "sessionId": session_id or "",
        "sport": str(data.get("sport") or "").upper(),
        "creatorRef": data.get("creatorRef") or data.get("creator_ref") or "",
        "source": data.get("source") or "app",
        "metadata": metadata,
        "createdAt": created_at,
        "userAgent": h.get("user-agent", ""),
        "ipAddress": client_ip(event) or "",
    }
    table().put_item(Item=item)
    return resp(201, {"ok": True, "stored": True, "eventId": event_id, "eventType": event_type, "createdAt": created_at})


def events_for_days(days: int, limit_per_day: int = 500) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for day in date_range(days):
        result = table().query(KeyConditionExpression=Key("PK").eq(f"ANALYTICS#{day}"), Limit=limit_per_day)
        out.extend(result.get("Items") or [])
    out.sort(key=lambda row: row.get("createdAt", ""), reverse=True)
    return out


def count_by(items: List[Dict[str, Any]], field: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in items:
        key = str(item.get(field) or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return counts


def handle_member_activity(event: Dict[str, Any]) -> Dict[str, Any]:
    q = query_params(event)
    try:
        days = max(1, min(int(q.get("days") or 1), 14))
    except Exception:
        days = 1
    try:
        limit = max(1, min(int(q.get("limit") or 50), 200))
    except Exception:
        limit = 50

    all_events = events_for_days(days)
    activity_events = [item for item in all_events if item.get("eventType") in ACTIVITY_EVENT_TYPES]
    member_ids = {item.get("memberId") for item in activity_events if item.get("memberId")}
    anonymous_ids = {item.get("anonymousId") for item in activity_events if item.get("anonymousId")}
    session_ids = {item.get("sessionId") for item in activity_events if item.get("sessionId")}
    sports = count_by([item for item in activity_events if item.get("sport")], "sport")
    by_event_type = count_by(activity_events, "eventType")
    by_source = count_by(activity_events, "source")

    per_member: Dict[str, Dict[str, Any]] = {}
    for item in activity_events:
        member_id = item.get("memberId") or "anonymous"
        row = per_member.setdefault(member_id, {"memberId": member_id, "events": 0, "eventTypes": {}, "lastSeenAt": "", "sports": {}})
        row["events"] += 1
        row["eventTypes"][item.get("eventType") or "unknown"] = row["eventTypes"].get(item.get("eventType") or "unknown", 0) + 1
        if item.get("sport"):
            row["sports"][item.get("sport")] = row["sports"].get(item.get("sport"), 0) + 1
        if item.get("createdAt", "") > row["lastSeenAt"]:
            row["lastSeenAt"] = item.get("createdAt", "")
    top_members = sorted(per_member.values(), key=lambda row: (row["events"], row["lastSeenAt"]), reverse=True)[:limit]

    return resp(200, {
        "ok": True,
        "dashboard": "member_activity",
        "windowDays": days,
        "totalActivityEvents": len(activity_events),
        "uniqueMembers": len(member_ids),
        "uniqueAnonymousUsers": len(anonymous_ids),
        "uniqueSessions": len(session_ids),
        "byEventType": by_event_type,
        "bySport": sports,
        "bySource": by_source,
        "topMembers": top_members,
        "recentEvents": activity_events[:limit],
        "notes": [
            "This dashboard uses analytics events collected by /v1/inqsi/analytics/event.",
            "It does not infer missing activity or fabricate usage.",
        ],
    })


def route(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    event = event or {}
    method = (event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method") or "GET").upper()
    path = (event.get("rawPath") or event.get("path") or "/").rstrip("/") or "/"
    if method == "OPTIONS" and (path.startswith("/v1/inqsi/analytics") or path.startswith("/v1/analytics") or path.startswith("/v1/inqsi/admin/analytics") or path.startswith("/v1/admin/analytics")):
        return resp(200, {"ok": True})
    if path in {"/v1/inqsi/analytics/health", "/v1/analytics/health"} and method == "GET":
        return handle_health()
    if path in {"/v1/inqsi/analytics/event", "/v1/analytics/event"} and method == "POST":
        return handle_event(event, parse_body(event))
    if path in {"/v1/inqsi/admin/analytics/members", "/v1/admin/analytics/members"} and method == "GET":
        return handle_member_activity(event)
    return None
