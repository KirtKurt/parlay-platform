import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

import boto3
from boto3.dynamodb.conditions import Attr


dynamodb = boto3.resource("dynamodb")
TABLE_NAME = os.environ.get("SNAPSHOTS_TABLE", "")


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


def qparams(event: Dict[str, Any]) -> Dict[str, str]:
    return event.get("queryStringParameters") or {}


def parse_dt(value: Any) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def minutes_between(start: Any, end: Any) -> Optional[int]:
    a = parse_dt(start)
    b = parse_dt(end)
    if not a or not b:
        return None
    return int(max(0, (b - a).total_seconds() // 60))


def scan_uploads(limit: int = 1000) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    start_key = None
    expression = Attr("record_type").eq("member_image_upload") | Attr("recordType").eq("member_image_upload")
    while True:
        args = {"FilterExpression": expression, "Limit": min(250, max(1, limit - len(items)))}
        if start_key:
            args["ExclusiveStartKey"] = start_key
        result = table().scan(**args)
        items.extend(result.get("Items") or [])
        if len(items) >= limit:
            return items[:limit]
        start_key = result.get("LastEvaluatedKey")
        if not start_key:
            return items


def inc(bucket: Dict[str, int], key: Any) -> None:
    k = str(key or "unknown")
    bucket[k] = bucket.get(k, 0) + 1


def handle(event: Dict[str, Any]) -> Dict[str, Any]:
    q = qparams(event)
    try:
        limit = max(1, min(int(q.get("limit") or 1000), 2000))
    except Exception:
        limit = 1000
    uploads = scan_uploads(limit)

    by_status: Dict[str, int] = {}
    by_reason: Dict[str, int] = {}
    by_role: Dict[str, int] = {}
    by_source: Dict[str, int] = {}
    by_member: Dict[str, Dict[str, Any]] = {}
    pending: List[Dict[str, Any]] = []
    visible = 0
    approved = 0
    rejected = 0
    manual_review = 0
    review_minutes: List[int] = []

    for item in uploads:
        status = item.get("moderation_status") or item.get("moderationStatus") or "unknown"
        reason = item.get("moderation_reason_code") or item.get("moderationReasonCode") or "unspecified"
        role = item.get("image_role") or item.get("imageRole") or "unspecified"
        source = item.get("moderation_reason_source") or item.get("moderationSource") or "unspecified"
        member = str(item.get("member_id") or item.get("memberId") or "unknown")
        inc(by_status, status)
        inc(by_reason, reason)
        inc(by_role, role)
        inc(by_source, source)
        member_row = by_member.setdefault(member, {"memberId": member, "uploads": 0, "approved": 0, "rejected": 0, "manualReview": 0})
        member_row["uploads"] += 1
        if status == "approved":
            approved += 1
            member_row["approved"] += 1
        if status == "rejected":
            rejected += 1
            member_row["rejected"] += 1
        if status in {"manual_review", "queued_for_scan", "pending"}:
            manual_review += 1
            member_row["manualReview"] += 1
            pending.append(item)
        if item.get("is_visible") or item.get("isVisible"):
            visible += 1
        minutes = minutes_between(item.get("created_at") or item.get("createdAt"), item.get("reviewed_at") or item.get("reviewedAt") or item.get("updated_at") or item.get("updatedAt"))
        if minutes is not None:
            review_minutes.append(minutes)

    avg_review = Decimal("0")
    if review_minutes:
        avg_review = Decimal(str(round(sum(review_minutes) / len(review_minutes), 2)))

    top_reasons = [{"reasonCode": k, "count": v} for k, v in sorted(by_reason.items(), key=lambda p: p[1], reverse=True)[:10]]
    top_members = sorted(by_member.values(), key=lambda row: (row.get("rejected", 0), row.get("manualReview", 0), row.get("uploads", 0)), reverse=True)[:20]
    oldest_pending = sorted(pending, key=lambda row: row.get("created_at") or row.get("createdAt") or "")[:20]

    return out(200, {
        "ok": True,
        "dashboard": "moderation_analytics",
        "recordsScanned": len(uploads),
        "summary": {
            "totalUploads": len(uploads),
            "approved": approved,
            "rejected": rejected,
            "manualOrQueued": manual_review,
            "visibleImages": visible,
            "averageReviewMinutes": avg_review,
        },
        "byStatus": by_status,
        "byReasonCode": by_reason,
        "byImageRole": by_role,
        "byModerationSource": by_source,
        "topReasonCodes": top_reasons,
        "membersNeedingReview": top_members,
        "oldestPending": oldest_pending,
        "notes": [
            "This dashboard reads stored member image moderation records only.",
            "It does not approve, reject, or alter images.",
        ],
    })


def route(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    event = event or {}
    method = (event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method") or "GET").upper()
    path = (event.get("rawPath") or event.get("path") or "/").rstrip("/") or "/"
    if method == "OPTIONS" and (path.startswith("/v1/inqsi/admin/analytics") or path.startswith("/v1/admin/analytics")):
        return out(200, {"ok": True})
    if path in {"/v1/inqsi/admin/analytics/moderation", "/v1/admin/analytics/moderation"} and method == "GET":
        return handle(event)
    return None
