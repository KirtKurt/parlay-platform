import json
import os
import time
import uuid
from decimal import Decimal
from typing import Any, Dict, List, Optional

import boto3
from boto3.dynamodb.conditions import Key


dynamodb = boto3.resource("dynamodb")
TABLE_NAME = os.environ.get("SNAPSHOTS_TABLE", "")

HIGH_RISK_PATH_PREFIXES = [
    "/v1/inqsi/admin/",
    "/v1/admin/",
    "/v1/inqsi/moderation/",
    "/v1/moderation/",
]
MEDIUM_RISK_PATH_PREFIXES = [
    "/v1/inqsi/analytics/event",
    "/v1/analytics/event",
    "/v1/inqsi/account-integrity/signup-check",
    "/v1/account-integrity/signup-check",
    "/v1/inqsi/member-images/upload",
    "/v1/member-images/upload",
    "/v1/inqsi/creators/track-visit",
    "/v1/inqsi/creators/track-convert",
]


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


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
            "access-control-allow-methods": "GET,POST,OPTIONS",
            "access-control-allow-headers": "content-type,authorization,x-inqsi-admin-token,x-inqsi-member-id,x-inqsi-session-id,x-inqsi-device-id",
        },
        "body": json.dumps(clean(body)),
    }


def table():
    if not TABLE_NAME:
        raise RuntimeError("SNAPSHOTS_TABLE is not configured")
    return dynamodb.Table(TABLE_NAME)


def body(event: Dict[str, Any]) -> Dict[str, Any]:
    try:
        payload = json.loads(event.get("body") or "{}")
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def headers(event: Dict[str, Any]) -> Dict[str, str]:
    return {str(k).lower(): str(v) for k, v in (event.get("headers") or {}).items() if v is not None}


def source_ip(event: Dict[str, Any], data: Optional[Dict[str, Any]] = None) -> str:
    data = data or {}
    rc = event.get("requestContext") or {}
    http = rc.get("http") or {}
    return str(data.get("ipAddress") or http.get("sourceIp") or rc.get("identity", {}).get("sourceIp") or "unknown")


def request_path(event: Dict[str, Any], data: Optional[Dict[str, Any]] = None) -> str:
    data = data or {}
    return str(data.get("path") or event.get("rawPath") or event.get("path") or "/")


def request_method(event: Dict[str, Any], data: Optional[Dict[str, Any]] = None) -> str:
    data = data or {}
    return str(data.get("method") or event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method") or "GET").upper()


def risk_for_path(path: str) -> str:
    for prefix in HIGH_RISK_PATH_PREFIXES:
        if path.startswith(prefix):
            return "HIGH"
    for prefix in MEDIUM_RISK_PATH_PREFIXES:
        if path.startswith(prefix):
            return "MEDIUM"
    return "LOW"


def base_score(event_type: str, path_risk: str) -> int:
    score = 0
    if path_risk == "HIGH":
        score += 45
    elif path_risk == "MEDIUM":
        score += 20
    if event_type in {"ADMIN_TOKEN_MISSING", "ADMIN_TOKEN_INVALID"}:
        score += 50
    if event_type in {"RATE_LIMIT_SIGNAL", "ANALYTICS_SPAM_SIGNAL", "CREATOR_REFERRAL_ABUSE_SIGNAL", "IMAGE_UPLOAD_ABUSE_SIGNAL"}:
        score += 40
    return min(score, 100)


def severity(score: int) -> str:
    if score >= 80:
        return "HIGH"
    if score >= 45:
        return "MEDIUM"
    return "LOW"


def counter_count(kind: str, value: str) -> int:
    if not value or value == "unknown":
        return 0
    try:
        res = table().query(KeyConditionExpression=Key("PK").eq(f"CYBER#{kind}#{today()}#{value}"), Limit=101)
        return len(res.get("Items") or [])
    except Exception:
        return 0


def record_security_event(event_type: str, event: Dict[str, Any], details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    details = details or {}
    h = headers(event)
    path = str(details.get("path") or request_path(event, details))
    method = str(details.get("method") or request_method(event, details)).upper()
    ip = str(details.get("ipAddress") or source_ip(event, details))
    device_id = str(details.get("deviceId") or details.get("device_id") or h.get("x-inqsi-device-id") or "unknown")
    session_id = str(details.get("sessionId") or details.get("session_id") or h.get("x-inqsi-session-id") or "")
    member_id = str(details.get("memberId") or details.get("member_id") or h.get("x-inqsi-member-id") or "")
    path_risk = risk_for_path(path)
    score = base_score(event_type, path_risk)
    ip_today = counter_count("IP", ip)
    device_today = counter_count("DEVICE", device_id)
    if ip_today >= 50:
        score = min(score + 25, 100)
    elif ip_today >= 20:
        score = min(score + 15, 100)
    if device_today >= 25:
        score = min(score + 25, 100)
    elif device_today >= 10:
        score = min(score + 15, 100)
    created_at = now()
    event_id = str(details.get("eventId") or f"cyber_{uuid.uuid4().hex[:18]}")
    item = {
        "PK": f"CYBER#{today()}",
        "SK": f"EVENT#{created_at}#{event_id}",
        "recordType": "cyber_security_event",
        "eventId": event_id,
        "eventType": event_type,
        "severity": severity(score),
        "riskScore": Decimal(str(score)),
        "pathRisk": path_risk,
        "path": path,
        "method": method,
        "ipAddress": ip,
        "deviceId": device_id,
        "sessionId": session_id,
        "memberId": member_id,
        "userAgent": h.get("user-agent", ""),
        "details": {k: v for k, v in details.items() if k not in {"adminToken", "token", "authorization"}},
        "createdAt": created_at,
    }
    table().put_item(Item=item)
    table().put_item(Item={"PK": f"CYBER#IP#{today()}#{ip}", "SK": f"EVENT#{created_at}#{event_id}", **item})
    if device_id and device_id != "unknown":
        table().put_item(Item={"PK": f"CYBER#DEVICE#{today()}#{device_id}", "SK": f"EVENT#{created_at}#{event_id}", **item})
    return item


def request_check(event: Dict[str, Any]) -> Dict[str, Any]:
    data = body(event)
    event_type = str(data.get("eventType") or "REQUEST_CHECK")
    item = record_security_event(event_type, event, data)
    return out(200, {
        "ok": True,
        "stored": True,
        "eventId": item["eventId"],
        "eventType": item["eventType"],
        "severity": item["severity"],
        "riskScore": item["riskScore"],
        "pathRisk": item["pathRisk"],
        "recommendedAction": "REVIEW" if item["severity"] in {"MEDIUM", "HIGH"} else "MONITOR",
    })


def events_for_day(day: str, limit: int = 500) -> List[Dict[str, Any]]:
    res = table().query(KeyConditionExpression=Key("PK").eq(f"CYBER#{day}"), Limit=limit)
    items = res.get("Items") or []
    return sorted(items, key=lambda x: x.get("createdAt", ""), reverse=True)


def summary_for_events(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_severity: Dict[str, int] = {}
    by_type: Dict[str, int] = {}
    by_path: Dict[str, int] = {}
    by_ip: Dict[str, int] = {}
    for item in events:
        by_severity[item.get("severity") or "unknown"] = by_severity.get(item.get("severity") or "unknown", 0) + 1
        by_type[item.get("eventType") or "unknown"] = by_type.get(item.get("eventType") or "unknown", 0) + 1
        by_path[item.get("path") or "unknown"] = by_path.get(item.get("path") or "unknown", 0) + 1
        ip = item.get("ipAddress") or "unknown"
        by_ip[ip] = by_ip.get(ip, 0) + 1
    top_ips = [{"ipAddress": k, "count": v} for k, v in sorted(by_ip.items(), key=lambda pair: pair[1], reverse=True)[:10]]
    return {
        "totalEvents": len(events),
        "high": by_severity.get("HIGH", 0),
        "medium": by_severity.get("MEDIUM", 0),
        "low": by_severity.get("LOW", 0),
        "bySeverity": by_severity,
        "byEventType": by_type,
        "byPath": by_path,
        "topIpAddresses": top_ips,
    }


def admin_events(event: Dict[str, Any]) -> Dict[str, Any]:
    q = event.get("queryStringParameters") or {}
    day = str(q.get("date") or today())
    try:
        limit = max(1, min(int(q.get("limit") or 100), 500))
    except Exception:
        limit = 100
    events = events_for_day(day, limit)
    return out(200, {"ok": True, "dashboard": "security_events", "date": day, "events": events})


def admin_summary(event: Dict[str, Any]) -> Dict[str, Any]:
    q = event.get("queryStringParameters") or {}
    day = str(q.get("date") or today())
    events = events_for_day(day, 500)
    return out(200, {
        "ok": True,
        "dashboard": "security_summary",
        "date": day,
        "summary": summary_for_events(events),
        "recentEvents": events[:25],
        "notes": ["Application-level security visibility only. WAF and API Gateway throttling are still separate infrastructure controls."],
    })


def security_alerts() -> List[Dict[str, Any]]:
    events = events_for_day(today(), 500)
    summary = summary_for_events(events)
    alerts = []
    if summary.get("high", 0) > 0:
        alerts.append({"severity": "HIGH", "code": "HIGH_SEVERITY_SECURITY_EVENTS", "message": "High-severity security events were logged today.", "source": "cyber_security", "details": {"count": summary.get("high")}})
    if summary.get("medium", 0) >= 10:
        alerts.append({"severity": "MEDIUM", "code": "SECURITY_EVENT_VOLUME", "message": "Ten or more medium-severity security events were logged today.", "source": "cyber_security", "details": {"count": summary.get("medium")}})
    for row in summary.get("topIpAddresses", [])[:3]:
        if row.get("count", 0) >= 25 and row.get("ipAddress") != "unknown":
            alerts.append({"severity": "MEDIUM", "code": "HIGH_REQUEST_VOLUME_IP", "message": "A single IP has high security-event volume today.", "source": "cyber_security", "details": row})
    return alerts


def route(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    event = event or {}
    method = (event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method") or "GET").upper()
    path = (event.get("rawPath") or event.get("path") or "/").rstrip("/") or "/"
    if method == "OPTIONS" and (path.startswith("/v1/inqsi/security") or path.startswith("/v1/security") or path.startswith("/v1/inqsi/admin/security") or path.startswith("/v1/admin/security")):
        return out(200, {"ok": True})
    if path in {"/v1/inqsi/security/request-check", "/v1/security/request-check"} and method == "POST":
        return request_check(event)
    if path in {"/v1/inqsi/admin/security/events", "/v1/admin/security/events"} and method == "GET":
        return admin_events(event)
    if path in {"/v1/inqsi/admin/security/summary", "/v1/admin/security/summary"} and method == "GET":
        return admin_summary(event)
    return None
