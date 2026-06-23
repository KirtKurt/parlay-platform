import json
import os
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set

import boto3
from boto3.dynamodb.conditions import Attr

try:
    import admin_alerts
except Exception:
    admin_alerts = None

try:
    import cyber_security
except Exception:
    cyber_security = None

try:
    import odds_live_ingestion
except Exception:
    odds_live_ingestion = None


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
            "access-control-allow-headers": "content-type,authorization,x-inqsi-member-id,x-inqsi-admin-token",
        },
        "body": json.dumps(clean(body)),
    }


def table():
    if not TABLE_NAME:
        raise RuntimeError("SNAPSHOTS_TABLE is not configured")
    return dynamodb.Table(TABLE_NAME)


def scan_creator_records(limit: int = 2000) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    start_key = None
    expression = (
        Attr("recordType").eq("creator_profile") |
        Attr("recordType").eq("creator_application") |
        Attr("recordType").eq("creator_attribution")
    )
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


def handle_for(item: Dict[str, Any]) -> str:
    if item.get("handle"):
        return str(item.get("handle"))
    ref = str(item.get("creatorRef") or "")
    if ref.startswith("cr_"):
        return ref[3:]
    pk = str(item.get("PK") or "")
    if pk.startswith("CREATOR#"):
        return pk.split("#", 1)[1]
    return "unknown"


def pct(numerator: int, denominator: int) -> Decimal:
    if denominator <= 0:
        return Decimal("0")
    return Decimal(str(round((numerator / denominator) * 100, 2)))


def public_creator(item: Dict[str, Any]) -> Dict[str, Any]:
    keys = ["handle", "creatorRef", "status", "displayName", "sportsFocus", "creatorTier", "verifiedCreator", "publicPageEnabled", "createdAt", "updatedAt"]
    return {k: item.get(k) for k in keys if item.get(k) is not None}


def handle(event: Dict[str, Any]) -> Dict[str, Any]:
    q = event.get("queryStringParameters") or {}
    try:
        limit = max(1, min(int(q.get("limit") or 2000), 3000))
    except Exception:
        limit = 2000

    records = scan_creator_records(limit)
    profiles = [r for r in records if r.get("recordType") == "creator_profile"]
    applications = [r for r in records if r.get("recordType") == "creator_application"]
    attributions = [r for r in records if r.get("recordType") == "creator_attribution"]
    visits = [r for r in attributions if r.get("touch") == "VISIT"]
    conversions = [r for r in attributions if r.get("touch") == "CONVERT"]

    by_status: Dict[str, int] = {}
    by_application_status: Dict[str, int] = {}
    by_tier: Dict[str, int] = {}
    by_sport_focus: Dict[str, int] = {}
    by_creator: Dict[str, Dict[str, Any]] = {}

    for profile in profiles:
        inc(by_status, profile.get("status"))
        inc(by_tier, profile.get("creatorTier"))
        for sport in profile.get("sportsFocus") or []:
            inc(by_sport_focus, sport)
        h = handle_for(profile)
        row = by_creator.setdefault(h, {"handle": h, "creatorRef": profile.get("creatorRef"), "status": profile.get("status"), "visits": 0, "conversions": 0, "uniqueVisitors": 0, "referredMembers": 0, "conversionRatePct": Decimal("0")})
        row["creator"] = public_creator(profile)

    for app in applications:
        inc(by_application_status, app.get("status"))

    visitor_sets: Dict[str, Set[str]] = {}
    member_sets: Dict[str, Set[str]] = {}
    for attr in attributions:
        h = handle_for(attr)
        row = by_creator.setdefault(h, {"handle": h, "creatorRef": attr.get("creatorRef"), "status": "unknown", "visits": 0, "conversions": 0, "uniqueVisitors": 0, "referredMembers": 0, "conversionRatePct": Decimal("0")})
        if attr.get("touch") == "VISIT":
            row["visits"] += 1
            visitor = attr.get("anonymousId") or attr.get("memberId")
            if visitor:
                visitor_sets.setdefault(h, set()).add(str(visitor))
        if attr.get("touch") == "CONVERT":
            row["conversions"] += 1
            member = attr.get("memberId")
            if member:
                member_sets.setdefault(h, set()).add(str(member))

    for h, row in by_creator.items():
        row["uniqueVisitors"] = len(visitor_sets.get(h, set()))
        row["referredMembers"] = len(member_sets.get(h, set()))
        row["conversionRatePct"] = pct(row.get("conversions", 0), row.get("visits", 0))
        row["payouts"] = "HELD_NOT_BUILT_YET"

    top_creators = sorted(by_creator.values(), key=lambda row: (row.get("conversions", 0), row.get("visits", 0), row.get("referredMembers", 0)), reverse=True)[:25]
    pending_apps = sorted([a for a in applications if a.get("status") in {"SUBMITTED", "PENDING", "MANUAL_REVIEW"}], key=lambda r: r.get("createdAt", ""))[:25]

    return out(200, {
        "ok": True,
        "dashboard": "creator_analytics",
        "recordsScanned": len(records),
        "adminAlertsDelegated": admin_alerts is not None,
        "cyberSecurityDelegated": cyber_security is not None,
        "oddsLiveIngestionDelegated": odds_live_ingestion is not None,
        "summary": {
            "creatorProfiles": len(profiles),
            "creatorApplications": len(applications),
            "pendingApplications": len(pending_apps),
            "attributionEvents": len(attributions),
            "creatorPageVisits": len(visits),
            "creatorConversions": len(conversions),
            "overallConversionRatePct": pct(len(conversions), len(visits)),
            "payouts": "HELD_NOT_BUILT_YET",
        },
        "byCreatorStatus": by_status,
        "byApplicationStatus": by_application_status,
        "byCreatorTier": by_tier,
        "bySportFocus": by_sport_focus,
        "topCreators": top_creators,
        "pendingApplications": pending_apps,
        "notes": [
            "This dashboard reads creator profile, application, and attribution records only.",
            "Payout, tax, and commission logic remain intentionally held.",
        ],
    })


def route(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    event = event or {}
    if odds_live_ingestion is not None:
        odds_routed = odds_live_ingestion.route(event)
        if odds_routed is not None:
            return odds_routed
    if cyber_security is not None:
        cyber_routed = cyber_security.route(event)
        if cyber_routed is not None:
            return cyber_routed
    if admin_alerts is not None:
        alert_routed = admin_alerts.route(event)
        if alert_routed is not None:
            return alert_routed
    method = (event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method") or "GET").upper()
    path = (event.get("rawPath") or event.get("path") or "/").rstrip("/") or "/"
    if method == "OPTIONS" and (path.startswith("/v1/inqsi/admin/analytics") or path.startswith("/v1/admin/analytics")):
        return out(200, {"ok": True})
    if path in {"/v1/inqsi/admin/analytics/creators", "/v1/admin/analytics/creators"} and method == "GET":
        return handle(event)
    return None
