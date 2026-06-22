import json
import os
import time
import uuid
from decimal import Decimal
from typing import Any, Dict, List, Optional

import boto3


dynamodb = boto3.resource("dynamodb")
TABLE_NAME = os.environ.get("SNAPSHOTS_TABLE", "")


RULES = {
    "ok": True,
    "portal": "influencer_creator_portal",
    "version": "v1",
    "payouts": "HELD_NOT_BUILT_YET",
    "allowed": [
        "sports-only creator identity",
        "public creator page",
        "verified slips and Inqis-generated score cards",
        "clean moderated profile and banner images",
        "trackable referral links",
        "approved promotional assets",
    ],
    "notAllowed": [
        "politics",
        "racial or hateful content",
        "profanity",
        "violence, weapons, threats, or gore",
        "nudity or sexual content",
        "uploaded images with readable words",
        "guaranteed winning language",
        "phrases like lock, free money, cannot lose, sure thing, or guaranteed bet",
        "gambling-income promises",
    ],
    "requiredDisclaimer": "Inqis is a sports analytics and risk-review platform. No creator content guarantees outcomes. Betting involves risk.",
}


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


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
        "headers": {"content-type": "application/json", "access-control-allow-origin": "*", "access-control-allow-methods": "GET,POST,PATCH,OPTIONS"},
        "body": json.dumps(safe(body)),
    }


def body(event: Dict[str, Any]) -> Dict[str, Any]:
    try:
        payload = json.loads(event.get("body") or "{}")
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def table():
    if not TABLE_NAME:
        raise RuntimeError("SNAPSHOTS_TABLE is not configured")
    return dynamodb.Table(TABLE_NAME)


def clean_handle(value: str) -> str:
    return "".join(ch for ch in str(value or "").lower().strip() if ch.isalnum() or ch in {"_", "-"})[:32]


def creator_ref(handle: str) -> str:
    return f"cr_{clean_handle(handle)}"


def base_url() -> str:
    return os.environ.get("INQSI_PUBLIC_BASE_URL", "https://inqis.app").rstrip("/")


def creator_pk(handle: str) -> str:
    return f"CREATOR#{clean_handle(handle)}"


def public_profile(item: Dict[str, Any]) -> Dict[str, Any]:
    fields = ["handle", "creatorRef", "status", "displayName", "bio", "sportsFocus", "favoriteTeams", "bettingPersonality", "verifiedCreator", "profileImageUrl", "bannerImageUrl", "socialLinks", "creatorTier", "publicPageEnabled", "createdAt", "updatedAt"]
    return {k: item.get(k) for k in fields if item.get(k) is not None}


def promo_assets(handle: str) -> Dict[str, Any]:
    ref = creator_ref(handle)
    join_url = f"{base_url()}/join?ref={ref}"
    return {
        "creatorPageUrl": f"{base_url()}/c/{clean_handle(handle)}",
        "joinUrl": join_url,
        "qrPayload": join_url,
        "shortCopy": "Scan your slip before you lock it in. Join Inqis through my page.",
        "disclaimerCopy": RULES["requiredDisclaimer"],
        "approvedHashtags": ["Inqis", "SlipScan", "SportsAnalytics", "BetSmarter"],
        "blockedPhrases": ["lock", "free money", "guaranteed", "cannot lose", "sure thing"],
    }


def get_creator(handle: str) -> Optional[Dict[str, Any]]:
    result = table().get_item(Key={"PK": creator_pk(handle), "SK": "PROFILE"})
    return result.get("Item")


def list_creator_items(handle: str, prefix: str, limit: int = 200) -> List[Dict[str, Any]]:
    result = table().query(KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)", ExpressionAttributeValues={":pk": creator_pk(handle), ":sk": prefix}, Limit=limit)
    return result.get("Items") or []


def stats(handle: str) -> Dict[str, Any]:
    attrs = list_creator_items(handle, "ATTR#", 500)
    visits = [a for a in attrs if a.get("touch") == "VISIT"]
    conversions = [a for a in attrs if a.get("touch") == "CONVERT"]
    members = {a.get("memberId") for a in conversions if a.get("memberId")}
    anon = {a.get("anonymousId") for a in attrs if a.get("anonymousId")}
    clicks = len(visits)
    signups = len(members)
    conversion_rate = round((signups / max(clicks, 1)) * 100, 2) if clicks else 0
    return {
        "clicks": clicks,
        "attributionEvents": len(attrs),
        "uniqueAnonymousVisitors": len(anon),
        "referredMembers": signups,
        "conversions": len(conversions),
        "conversionRatePct": Decimal(str(conversion_rate)),
        "estimatedPayout": "HELD_UNTIL_PAYOUT_MODULE_BUILT",
    }


def handle_apply(data: Dict[str, Any]) -> Dict[str, Any]:
    email = str(data.get("email") or "").strip().lower()
    requested = clean_handle(data.get("requestedHandle") or data.get("handle") or "")
    if not email or not requested:
        return resp(400, {"error": "email_and_requestedHandle_required"})
    item = {
        "PK": "CREATOR_APPLICATIONS",
        "SK": f"APP#{now()}#{uuid.uuid4().hex[:10]}",
        "recordType": "creator_application",
        "status": "SUBMITTED",
        "email": email,
        "requestedHandle": requested,
        "displayName": data.get("displayName"),
        "sportsFocus": data.get("sportsFocus") or [],
        "socialLinks": data.get("socialLinks") or {},
        "followerCount": data.get("followerCount"),
        "whyInqis": data.get("whyInqis"),
        "agreedToCreatorRules": bool(data.get("agreedToCreatorRules")),
        "createdAt": now(),
        "updatedAt": now(),
        "payouts": "HELD_NOT_BUILT_YET",
    }
    table().put_item(Item={k: v for k, v in item.items() if v is not None})
    return resp(201, {"ok": True, "application": item, "rules": RULES})


def handle_admin_list() -> Dict[str, Any]:
    creators = []
    apps = []
    start_key = None
    while True:
        args: Dict[str, Any] = {"Limit": 200}
        if start_key:
            args["ExclusiveStartKey"] = start_key
        result = table().scan(**args)
        for item in result.get("Items") or []:
            if item.get("recordType") == "creator_profile":
                creators.append(public_profile(item))
            if item.get("recordType") == "creator_application":
                apps.append(item)
        start_key = result.get("LastEvaluatedKey")
        if not start_key:
            break
    return resp(200, {"ok": True, "creators": creators, "applications": apps, "payouts": "HELD_NOT_BUILT_YET"})


def handle_admin_upsert(handle: str, data: Dict[str, Any], action: str) -> Dict[str, Any]:
    h = clean_handle(handle or data.get("handle") or data.get("requestedHandle") or "")
    if not h:
        return resp(400, {"error": "handle_required"})
    current = get_creator(h) or {}
    status = current.get("status") or "DRAFT"
    if action == "approve":
        status = "APPROVED"
    elif action == "reject":
        status = "REJECTED"
    elif action == "suspend":
        status = "SUSPENDED"
    elif action == "restore":
        status = "APPROVED"
    item = {
        **current,
        "PK": creator_pk(h),
        "SK": "PROFILE",
        "recordType": "creator_profile",
        "handle": h,
        "creatorRef": creator_ref(h),
        "status": status,
        "displayName": data.get("displayName", current.get("displayName", h)),
        "bio": data.get("bio", current.get("bio", "")),
        "sportsFocus": data.get("sportsFocus", current.get("sportsFocus", [])),
        "favoriteTeams": data.get("favoriteTeams", current.get("favoriteTeams", [])),
        "bettingPersonality": data.get("bettingPersonality", current.get("bettingPersonality", "")),
        "verifiedCreator": bool(data.get("verifiedCreator", status == "APPROVED")),
        "profileImageUrl": data.get("profileImageUrl", current.get("profileImageUrl", "")),
        "bannerImageUrl": data.get("bannerImageUrl", current.get("bannerImageUrl", "")),
        "socialLinks": data.get("socialLinks", current.get("socialLinks", {})),
        "creatorTier": data.get("creatorTier", current.get("creatorTier", "STANDARD")),
        "publicPageEnabled": bool(data.get("publicPageEnabled", status == "APPROVED")),
        "createdAt": current.get("createdAt") or now(),
        "updatedAt": now(),
        "payouts": "HELD_NOT_BUILT_YET",
    }
    table().put_item(Item=item)
    return resp(200, {"ok": True, "creator": public_profile(item), "promo": promo_assets(h), "rules": RULES})


def handle_public(handle: str) -> Dict[str, Any]:
    item = get_creator(handle)
    if not item or item.get("status") != "APPROVED" or item.get("publicPageEnabled") is False:
        return resp(404, {"error": "creator_not_found"})
    return resp(200, {"ok": True, "creator": public_profile(item), "stats": stats(handle), "promo": promo_assets(handle), "rules": RULES})


def handle_visit(data: Dict[str, Any]) -> Dict[str, Any]:
    ref = data.get("creatorRef") or data.get("creator_ref") or data.get("ref")
    handle = clean_handle(data.get("handle") or str(ref or "").replace("cr_", ""))
    if not handle:
        return resp(400, {"error": "creator_handle_or_ref_required"})
    item = {
        "PK": creator_pk(handle),
        "SK": f"ATTR#{now()}#{uuid.uuid4().hex[:10]}",
        "recordType": "creator_attribution",
        "touch": "VISIT",
        "creatorRef": creator_ref(handle),
        "anonymousId": data.get("anonymousId") or data.get("anonymous_id") or f"anon_{uuid.uuid4().hex[:12]}",
        "memberId": data.get("memberId") or data.get("member_id"),
        "landingPath": data.get("landingPath") or data.get("landing_path"),
        "utmSource": data.get("utmSource"),
        "utmMedium": data.get("utmMedium"),
        "utmCampaign": data.get("utmCampaign"),
        "createdAt": now(),
    }
    table().put_item(Item={k: v for k, v in item.items() if v is not None})
    return resp(201, {"ok": True, "stored": True, "attribution": item})


def handle_convert(data: Dict[str, Any]) -> Dict[str, Any]:
    ref = data.get("creatorRef") or data.get("creator_ref") or data.get("ref")
    handle = clean_handle(data.get("handle") or str(ref or "").replace("cr_", ""))
    member_id = data.get("memberId") or data.get("member_id")
    if not handle or not member_id:
        return resp(400, {"error": "creator_handle_or_ref_and_memberId_required"})
    item = {
        "PK": creator_pk(handle),
        "SK": f"ATTR#{now()}#{uuid.uuid4().hex[:10]}",
        "recordType": "creator_attribution",
        "touch": "CONVERT",
        "creatorRef": creator_ref(handle),
        "memberId": member_id,
        "anonymousId": data.get("anonymousId") or data.get("anonymous_id"),
        "convertedAt": now(),
        "createdAt": now(),
    }
    table().put_item(Item={k: v for k, v in item.items() if v is not None})
    return resp(201, {"ok": True, "stored": True, "attribution": item})


def route(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    event = event or {}
    method = (event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method") or "GET").upper()
    path = (event.get("rawPath") or event.get("path") or "/").rstrip("/") or "/"
    data = body(event)
    if path in {"/v1/inqsi/creators/rules", "/v1/creators/rules"} and method == "GET":
        return resp(200, RULES)
    if path in {"/v1/inqsi/creators/apply", "/v1/creators/apply"} and method == "POST":
        return handle_apply(data)
    if path in {"/v1/inqsi/creators", "/v1/creators"} and method == "GET":
        return handle_admin_list()
    if path in {"/v1/inqsi/creators/track-visit", "/v1/creators/track-visit"} and method == "POST":
        return handle_visit(data)
    if path in {"/v1/inqsi/creators/track-convert", "/v1/creators/track-convert"} and method == "POST":
        return handle_convert(data)
    if path in {"/v1/inqsi/creators/dashboard", "/v1/creators/dashboard"} and method == "GET":
        qs = event.get("queryStringParameters") or {}
        handle = clean_handle(qs.get("handle") or headers(event).get("x-inqsi-creator-handle") or "")
        if not handle:
            return resp(400, {"error": "handle_required"})
        item = get_creator(handle)
        return resp(200, {"ok": True, "creator": public_profile(item or {"handle": handle, "creatorRef": creator_ref(handle), "status": "DRAFT"}), "stats": stats(handle), "promo": promo_assets(handle), "rules": RULES, "payouts": "HELD_NOT_BUILT_YET"})
    if path.startswith("/v1/inqsi/admin/creators/") or path.startswith("/v1/admin/creators/"):
        parts = path.split("/")
        handle = parts[-2] if parts[-1] in {"approve", "reject", "suspend", "restore", "update"} else parts[-1]
        action = parts[-1] if parts[-1] in {"approve", "reject", "suspend", "restore", "update"} else "update"
        if method in {"POST", "PATCH"}:
            return handle_admin_upsert(handle, data, action)
    if path.startswith("/v1/inqsi/creators/") and method == "GET":
        return handle_public(path.split("/")[-1])
    if path.startswith("/v1/creators/") and method == "GET":
        return handle_public(path.split("/")[-1])
    return None
