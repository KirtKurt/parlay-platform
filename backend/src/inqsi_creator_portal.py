from __future__ import annotations

import json
import os
import uuid
from decimal import Decimal
from typing import Any, Dict, List, Optional

import boto3
from boto3.dynamodb.conditions import Key


DDB = boto3.resource("dynamodb")


CORS_HEADERS = {
    "Access-Control-Allow-Origin": os.environ.get("CORS_ALLOW_ORIGIN", "*"),
    "Access-Control-Allow-Headers": "content-type,authorization,x-inqsi-member-id,x-inqsi-admin-token",
    "Access-Control-Allow-Methods": "GET,POST,PATCH,DELETE,OPTIONS",
}


TABLE_ENV = {
    "members": "MEMBERS_TABLE",
    "saved_slips": "SAVED_SLIPS_TABLE",
    "score_history": "SCORE_HISTORY_TABLE",
    "creator_attribution": "CREATOR_ATTRIBUTION_TABLE",
    "support_notes": "SUPPORT_NOTES_TABLE",
    "admin_audit_logs": "ADMIN_AUDIT_LOGS_TABLE",
    "social_accounts": "SOCIAL_ACCOUNTS_TABLE",
}


CREATOR_RULES = {
    "ok": True,
    "version": "creator-portal-v1",
    "allowed": [
        "sports-only creator profile",
        "verified public slips",
        "Inqis-generated score cards",
        "clean creator images approved by moderation",
        "creator referral link sharing",
    ],
    "notAllowed": [
        "political content",
        "racial or hateful content",
        "profanity",
        "violence, weapons, threats, or gore",
        "nudity or sexual content",
        "uploaded images with readable words",
        "guaranteed winning language",
        "phrases like lock, free money, cannot lose, guaranteed bet, or sure thing",
        "direct gambling-income promises",
    ],
    "requiredDisclaimer": "Inqis is a sports analytics and risk-review platform. No creator content guarantees outcomes. Betting involves risk.",
    "payouts": "HELD_NOT_BUILT_YET",
}


PUBLIC_CREATOR_FIELDS = [
    "member_id",
    "display_name",
    "handle",
    "creator_ref",
    "creator_status",
    "creator_tier",
    "primary_sport",
    "sports_focus",
    "favorite_teams",
    "betting_personality",
    "bio",
    "verified_creator",
    "profile_image_url",
    "banner_image_url",
    "social_links",
    "public_profile_enabled",
    "public_score_enabled",
    "creator_page_enabled",
]


def to_json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    if isinstance(value, list):
        return [to_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: to_json_safe(item) for key, item in value.items()}
    return value


def response(status: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {"statusCode": status, "headers": CORS_HEADERS, "body": json.dumps(to_json_safe(body))}


def parse_body(event: Dict[str, Any]) -> Dict[str, Any]:
    try:
        payload = json.loads(event.get("body") or "{}")
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def utc_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def table(name: str):
    env_name = TABLE_ENV[name]
    table_name = os.environ.get(env_name)
    if not table_name:
        raise RuntimeError(f"{env_name} is not configured")
    return DDB.Table(table_name)


def headers(event: Dict[str, Any]) -> Dict[str, str]:
    return {str(k).lower(): str(v) for k, v in (event.get("headers") or {}).items() if v is not None}


def member_id_from_event(event: Dict[str, Any]) -> Optional[str]:
    h = headers(event)
    return h.get("x-inqsi-member-id") or h.get("x-member-id")


def require_member(event: Dict[str, Any]) -> str:
    member_id = member_id_from_event(event)
    if not member_id:
        raise PermissionError("x-inqsi-member-id header is required")
    return member_id


def require_owner(event: Dict[str, Any]) -> str:
    expected = os.environ.get("INQSI_ADMIN_API_TOKEN", "").strip()
    if not expected:
        raise RuntimeError("INQSI_ADMIN_API_TOKEN is not configured")
    if headers(event).get("x-inqsi-admin-token", "") != expected:
        raise PermissionError("valid x-inqsi-admin-token header is required")
    return member_id_from_event(event) or "owner"


def clean_handle(value: str) -> str:
    return "".join(ch for ch in str(value or "").lower().strip() if ch.isalnum() or ch in {"_", "-"})[:32]


def public_creator(member: Dict[str, Any]) -> Dict[str, Any]:
    return {key: member.get(key) for key in PUBLIC_CREATOR_FIELDS if member.get(key) is not None}


def creator_ref_for(handle: str) -> str:
    return f"cr_{clean_handle(handle)}"


def base_url() -> str:
    return os.environ.get("INQSI_PUBLIC_BASE_URL", "https://inqis.app").rstrip("/")


def promo_assets(handle: str, creator_ref: str) -> Dict[str, Any]:
    join_url = f"{base_url()}/join?ref={creator_ref}"
    page_url = f"{base_url()}/c/{handle}"
    return {
        "creatorPageUrl": page_url,
        "joinUrl": join_url,
        "qrPayload": join_url,
        "shortCopy": "Scan your slip before you lock it in. Join Inqis through my page.",
        "disclaimerCopy": CREATOR_RULES["requiredDisclaimer"],
        "approvedHashtags": ["Inqis", "SlipScan", "SportsAnalytics", "BetSmarter"],
        "blockedPhrases": ["lock", "free money", "guaranteed", "cannot lose", "sure thing"],
    }


def audit(actor: str, action: str, target_type: str, target_id: str, metadata: Optional[Dict[str, Any]] = None) -> None:
    item = {
        "target_id": target_id,
        "audit_id": f"{utc_now()}#{action}#{uuid.uuid4().hex[:10]}",
        "actor_member_id": actor,
        "action": action,
        "target_type": target_type,
        "created_at": utc_now(),
        "metadata": metadata or {},
    }
    table("admin_audit_logs").put_item(Item=item)


def scan_all(name: str, limit: int = 500) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    start_key = None
    while len(items) < limit:
        args: Dict[str, Any] = {"Limit": min(100, limit - len(items))}
        if start_key:
            args["ExclusiveStartKey"] = start_key
        result = table(name).scan(**args)
        items.extend(result.get("Items") or [])
        start_key = result.get("LastEvaluatedKey")
        if not start_key:
            break
    return items


def get_member(member_id: str) -> Optional[Dict[str, Any]]:
    return table("members").get_item(Key={"member_id": member_id}).get("Item")


def get_creator_by_handle(handle: str) -> Optional[Dict[str, Any]]:
    result = table("members").query(IndexName="HandleIndex", KeyConditionExpression=Key("handle").eq(clean_handle(handle)), Limit=1)
    items = result.get("Items") or []
    return items[0] if items else None


def get_creator_by_ref(creator_ref: str) -> Optional[Dict[str, Any]]:
    result = table("members").query(IndexName="CreatorRefIndex", KeyConditionExpression=Key("creator_ref").eq(creator_ref), Limit=1)
    items = result.get("Items") or []
    return items[0] if items else None


def attribution_items(creator_ref: str, limit: int = 500) -> List[Dict[str, Any]]:
    try:
        result = table("creator_attribution").query(IndexName="CreatorRefIndex", KeyConditionExpression=Key("creator_ref").eq(creator_ref), Limit=limit)
        return result.get("Items") or []
    except Exception:
        return [item for item in scan_all("creator_attribution", limit) if item.get("creator_ref") == creator_ref]


def public_slips(member_id: str, limit: int = 10) -> List[Dict[str, Any]]:
    result = table("saved_slips").query(KeyConditionExpression=Key("member_id").eq(member_id), Limit=100)
    slips = [item for item in result.get("Items") or [] if str(item.get("visibility") or "").upper() == "PUBLIC"]
    slips.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return slips[:limit]


def latest_scores(member_id: str, limit: int = 10) -> List[Dict[str, Any]]:
    result = table("score_history").query(KeyConditionExpression=Key("member_id").eq(member_id), Limit=100)
    scores = result.get("Items") or []
    scores.sort(key=lambda item: item.get("calculated_at", ""), reverse=True)
    return scores[:limit]


def stats_for(creator_ref: str) -> Dict[str, Any]:
    items = attribution_items(creator_ref)
    click_items = [i for i in items if not i.get("converted_at")]
    conversions = [i for i in items if i.get("converted_at") or i.get("member_id")]
    unique_anon = {i.get("anonymous_id") for i in items if i.get("anonymous_id")}
    unique_members = {i.get("member_id") for i in items if i.get("member_id")}
    clicks = len(click_items)
    signups = len(unique_members)
    conversion_rate = round((signups / max(clicks, 1)) * 100, 2) if clicks else 0
    return {
        "clicks": clicks,
        "attributionEvents": len(items),
        "uniqueAnonymousVisitors": len(unique_anon),
        "referredMembers": signups,
        "conversions": len(conversions),
        "conversionRatePct": Decimal(str(conversion_rate)),
        "estimatedPayout": "HELD_UNTIL_PAYOUT_MODULE_BUILT",
    }


def handle_creator_apply(body: Dict[str, Any]) -> Dict[str, Any]:
    email = str(body.get("email") or "").strip().lower()
    requested_handle = clean_handle(body.get("requestedHandle") or body.get("handle") or "")
    if not email or not requested_handle:
        return response(400, {"error": "email_and_requestedHandle_required"})
    now = utc_now()
    support_id = f"creator_app_{uuid.uuid4().hex[:16]}"
    item = {
        "member_id": f"creator_app#{email}",
        "support_id": support_id,
        "status": "SUBMITTED",
        "type": "CREATOR_APPLICATION",
        "created_at": now,
        "updated_at": now,
        "email": email,
        "requested_handle": requested_handle,
        "display_name": body.get("displayName"),
        "sports_focus": body.get("sportsFocus") or [],
        "social_links": body.get("socialLinks") or {},
        "follower_count": body.get("followerCount"),
        "why_inqis": body.get("whyInqis"),
        "agreed_to_creator_rules": bool(body.get("agreedToCreatorRules")),
        "rules_version": CREATOR_RULES["version"],
        "payout_module": "HELD_NOT_BUILT_YET",
    }
    table("support_notes").put_item(Item={k: v for k, v in item.items() if v is not None})
    return response(201, {"ok": True, "application": item, "creatorRules": CREATOR_RULES})


def handle_public_creator(handle: str) -> Dict[str, Any]:
    creator = get_creator_by_handle(handle)
    if not creator or str(creator.get("creator_status") or "").upper() != "APPROVED":
        return response(404, {"error": "creator_not_found"})
    if creator.get("creator_page_enabled") is False:
        return response(403, {"error": "creator_page_disabled"})
    creator_ref = creator.get("creator_ref") or creator_ref_for(creator.get("handle") or handle)
    return response(200, {
        "ok": True,
        "creator": public_creator(creator),
        "stats": stats_for(creator_ref),
        "publicSlips": public_slips(creator["member_id"]),
        "scoreHistory": latest_scores(creator["member_id"]),
        "promo": promo_assets(creator.get("handle") or handle, creator_ref),
        "rules": CREATOR_RULES,
    })


def handle_creator_dashboard(event: Dict[str, Any]) -> Dict[str, Any]:
    member_id = require_member(event)
    member = get_member(member_id)
    if not member:
        return response(404, {"error": "member_not_found"})
    creator_ref = member.get("creator_ref") or creator_ref_for(member.get("handle") or member_id)
    return response(200, {
        "ok": True,
        "creator": public_creator(member),
        "stats": stats_for(creator_ref),
        "publicSlips": public_slips(member_id, 25),
        "scoreHistory": latest_scores(member_id, 25),
        "promo": promo_assets(member.get("handle") or member_id, creator_ref),
        "payouts": {"status": "HELD", "message": "Payout reporting is intentionally not built until creator features 1-5 and 7-10 are complete."},
    })


def handle_creator_profile_patch(event: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
    member_id = require_member(event)
    member = get_member(member_id)
    if not member:
        return response(404, {"error": "member_not_found"})
    allowed = {
        "display_name", "bio", "primary_sport", "sports_focus", "favorite_teams", "betting_personality",
        "profile_image_url", "banner_image_url", "social_links", "public_profile_enabled", "public_score_enabled", "creator_page_enabled",
    }
    updates = {key: body[key] for key in allowed if key in body}
    if "handle" in body:
        updates["handle"] = clean_handle(body["handle"])
        updates["creator_ref"] = creator_ref_for(updates["handle"])
    if not updates:
        return response(400, {"error": "no_supported_fields"})
    updates["updated_at"] = utc_now()
    expr = "SET " + ", ".join(f"#{k}=:{k}" for k in updates)
    table("members").update_item(Key={"member_id": member_id}, UpdateExpression=expr, ExpressionAttributeNames={f"#{k}": k for k in updates}, ExpressionAttributeValues={f":{k}": v for k, v in updates.items()})
    return response(200, {"ok": True, "updated": sorted(updates.keys())})


def handle_creator_assets(event: Dict[str, Any]) -> Dict[str, Any]:
    member_id = require_member(event)
    member = get_member(member_id)
    if not member:
        return response(404, {"error": "member_not_found"})
    handle = member.get("handle") or member_id
    creator_ref = member.get("creator_ref") or creator_ref_for(handle)
    return response(200, {"ok": True, "assets": promo_assets(handle, creator_ref), "rules": CREATOR_RULES})


def handle_admin_creator_list(event: Dict[str, Any]) -> Dict[str, Any]:
    require_owner(event)
    members = scan_all("members", 500)
    creators = [m for m in members if m.get("creator_ref") or str(m.get("role") or "").upper() == "CREATOR" or m.get("creator_status")]
    applications = [item for item in scan_all("support_notes", 500) if item.get("type") == "CREATOR_APPLICATION"]
    return response(200, {"ok": True, "creators": [public_creator(c) for c in creators], "applications": applications, "payouts": "HELD_NOT_BUILT_YET"})


def handle_admin_creator_update(event: Dict[str, Any], member_id: str, body: Dict[str, Any], action: str = "update") -> Dict[str, Any]:
    actor = require_owner(event)
    member = get_member(member_id)
    if not member:
        return response(404, {"error": "member_not_found"})
    now = utc_now()
    updates: Dict[str, Any] = {"updated_at": now}
    if action == "approve":
        handle = clean_handle(body.get("handle") or member.get("handle") or member_id)
        updates.update({"role": "CREATOR", "creator_status": "APPROVED", "verified_creator": True, "handle": handle, "creator_ref": creator_ref_for(handle), "creator_page_enabled": True, "public_profile_enabled": True})
    elif action == "reject":
        updates.update({"creator_status": "REJECTED", "creator_page_enabled": False})
    elif action == "suspend":
        updates.update({"creator_status": "SUSPENDED", "creator_page_enabled": False})
    elif action == "restore":
        updates.update({"creator_status": "APPROVED", "creator_page_enabled": True})
    else:
        allowed = {"creator_status", "creator_tier", "verified_creator", "creator_page_enabled", "bio", "sports_focus", "favorite_teams", "betting_personality", "social_links"}
        updates.update({k: body[k] for k in allowed if k in body})
    expr = "SET " + ", ".join(f"#{k}=:{k}" for k in updates)
    table("members").update_item(Key={"member_id": member_id}, UpdateExpression=expr, ExpressionAttributeNames={f"#{k}": k for k in updates}, ExpressionAttributeValues={f":{k}": v for k, v in updates.items()})
    audit(actor, f"CREATOR_{action.upper()}", "creator", member_id, {"updates": sorted(updates.keys())})
    updated = get_member(member_id) or {"member_id": member_id, **updates}
    return response(200, {"ok": True, "creator": public_creator(updated), "payouts": "HELD_NOT_BUILT_YET"})


def handle_attribution_visit(body: Dict[str, Any]) -> Dict[str, Any]:
    creator_ref = body.get("creatorRef") or body.get("creator_ref") or body.get("ref")
    if not creator_ref:
        return response(400, {"error": "creatorRef_required"})
    now = utc_now()
    item = {
        "attribution_id": f"attr_{uuid.uuid4().hex[:16]}",
        "anonymous_id": body.get("anonymousId") or body.get("anonymous_id") or f"anon_{uuid.uuid4().hex[:12]}",
        "member_id": body.get("memberId") or body.get("member_id"),
        "creator_ref": creator_ref,
        "touch": body.get("touch") or "VISIT",
        "first_seen_at": now,
        "last_seen_at": now,
        "landing_path": body.get("landingPath") or body.get("landing_path"),
        "utm_source": body.get("utmSource"),
        "utm_medium": body.get("utmMedium"),
        "utm_campaign": body.get("utmCampaign"),
    }
    table("creator_attribution").put_item(Item={k: v for k, v in item.items() if v is not None})
    return response(201, {"ok": True, "stored": True, "attribution": item})


def handle_attribution_convert(event: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
    member_id = require_member(event)
    creator_ref = body.get("creatorRef") or body.get("creator_ref") or body.get("ref")
    if not creator_ref:
        return response(400, {"error": "creatorRef_required"})
    now = utc_now()
    item = {
        "attribution_id": f"attr_{uuid.uuid4().hex[:16]}",
        "member_id": member_id,
        "anonymous_id": body.get("anonymousId") or body.get("anonymous_id"),
        "creator_ref": creator_ref,
        "touch": "CONVERT",
        "first_seen_at": body.get("firstSeenAt") or now,
        "last_seen_at": now,
        "converted_at": now,
    }
    table("creator_attribution").put_item(Item={k: v for k, v in item.items() if v is not None})
    table("members").update_item(Key={"member_id": member_id}, UpdateExpression="SET creator_ref=:creator_ref, updated_at=:updated_at", ExpressionAttributeValues={":creator_ref": creator_ref, ":updated_at": now})
    return response(201, {"ok": True, "stored": True, "attribution": item})


def route(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    method = (event.get("requestContext", {}).get("http", {}).get("method") or event.get("httpMethod") or "GET").upper()
    path = (event.get("rawPath") or event.get("path") or "/").rstrip("/") or "/"
    body = parse_body(event)
    if method == "OPTIONS":
        return response(200, {"ok": True})
    if path == "/v1/public/creators/rules" and method == "GET":
        return response(200, CREATOR_RULES)
    if path == "/v1/public/creators/apply" and method == "POST":
        return handle_creator_apply(body)
    if path.startswith("/v1/public/creators/") and method == "GET":
        return handle_public_creator(path.split("/")[-1])
    if path == "/v1/members/creator/dashboard" and method == "GET":
        return handle_creator_dashboard(event)
    if path == "/v1/members/creator/profile" and method == "PATCH":
        return handle_creator_profile_patch(event, body)
    if path == "/v1/members/creator/assets" and method == "GET":
        return handle_creator_assets(event)
    if path == "/v1/members/creator/rules" and method == "GET":
        return response(200, CREATOR_RULES)
    if path == "/v1/attribution/creator-visit" and method == "POST":
        return handle_attribution_visit(body)
    if path == "/v1/attribution/creator-convert" and method == "POST":
        return handle_attribution_convert(event, body)
    if path == "/v1/admin/creators" and method == "GET":
        return handle_admin_creator_list(event)
    if path.startswith("/v1/admin/creators/"):
        parts = path.split("/")
        member_id = parts[4] if len(parts) > 4 else ""
        action = parts[5] if len(parts) > 5 else "update"
        if method in {"POST", "PATCH"} and action in {"approve", "reject", "suspend", "restore", "update"}:
            return handle_admin_creator_update(event, member_id, body, action)
    return None
