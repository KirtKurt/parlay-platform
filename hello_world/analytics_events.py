import json
import os
import time
import uuid
from decimal import Decimal
from typing import Any, Dict, Optional

import boto3


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


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def date_key(ts: str) -> str:
    return ts[:10]


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
        "supportedEventTypes": sorted(ALLOWED_EVENT_TYPES),
        "nextBuilds": [
            "member_activity_dashboard",
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


def route(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    event = event or {}
    method = (event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method") or "GET").upper()
    path = (event.get("rawPath") or event.get("path") or "/").rstrip("/") or "/"
    if method == "OPTIONS" and (path.startswith("/v1/inqsi/analytics") or path.startswith("/v1/analytics")):
        return resp(200, {"ok": True})
    if path in {"/v1/inqsi/analytics/health", "/v1/analytics/health"} and method == "GET":
        return handle_health()
    if path in {"/v1/inqsi/analytics/event", "/v1/analytics/event"} and method == "POST":
        return handle_event(event, parse_body(event))
    return None
