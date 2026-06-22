"""Deployable InQsi backend API foundation.

This Lambda exposes real storage-backed member, slip, score, attribution,
subscription, social account, and admin routes. It intentionally refuses to
pretend a provider is wired when OAuth/payment/scoring providers are missing.
"""

from __future__ import annotations

import json
import os
import uuid
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import boto3
from boto3.dynamodb.conditions import Key

from inqsi_backend_contracts import MAX_PARLAY_LEGS, route_manifest, utc_now

CORS_HEADERS = {
    "Access-Control-Allow-Origin": os.environ.get("CORS_ALLOW_ORIGIN", "*"),
    "Access-Control-Allow-Headers": "content-type,authorization,x-inqsi-member-id,x-inqsi-admin-token",
    "Access-Control-Allow-Methods": "GET,POST,PATCH,DELETE,OPTIONS",
}

DDB = boto3.resource("dynamodb")

TABLE_ENV = {
    "members": "MEMBERS_TABLE",
    "social_accounts": "SOCIAL_ACCOUNTS_TABLE",
    "saved_slips": "SAVED_SLIPS_TABLE",
    "score_history": "SCORE_HISTORY_TABLE",
    "creator_attribution": "CREATOR_ATTRIBUTION_TABLE",
    "subscriptions": "SUBSCRIPTIONS_TABLE",
    "admin_audit_logs": "ADMIN_AUDIT_LOGS_TABLE",
    "support_notes": "SUPPORT_NOTES_TABLE",
    "feature_flags": "FEATURE_FLAGS_TABLE",
}

PUBLIC_MEMBER_FIELDS = [
    "member_id",
    "display_name",
    "handle",
    "primary_sport",
    "public_profile_enabled",
    "public_score_enabled",
    "creator_ref",
]


def to_json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        if value % 1 == 0:
            return int(value)
        return float(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, list):
        return [to_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: to_json_safe(item) for key, item in value.items()}
    return value


def response(status_code: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": CORS_HEADERS,
        "body": json.dumps(to_json_safe(body)),
    }


def parse_body(event: Dict[str, Any]) -> Dict[str, Any]:
    raw_body = event.get("body")
    if not raw_body:
        return {}
    try:
        return json.loads(raw_body)
    except json.JSONDecodeError:
        return {}


def method_and_path(event: Dict[str, Any]) -> Tuple[str, str]:
    request_context = event.get("requestContext", {})
    method = request_context.get("http", {}).get("method") or request_context.get("httpMethod") or event.get("httpMethod") or "GET"
    path = event.get("rawPath") or event.get("path") or "/"
    return method.upper(), path.rstrip("/") or "/"


def query_params(event: Dict[str, Any]) -> Dict[str, str]:
    return event.get("queryStringParameters") or {}


def normalized_headers(event: Dict[str, Any]) -> Dict[str, str]:
    headers = event.get("headers") or {}
    return {str(key).lower(): str(value) for key, value in headers.items() if value is not None}


def table(name: str):
    env_name = TABLE_ENV[name]
    table_name = os.environ.get(env_name)
    if not table_name:
        raise RuntimeError(f"{env_name} is not configured")
    return DDB.Table(table_name)


def member_id_from_event(event: Dict[str, Any]) -> Optional[str]:
    headers = normalized_headers(event)
    return headers.get("x-inqsi-member-id") or headers.get("x-member-id")


def require_member(event: Dict[str, Any]) -> str:
    member_id = member_id_from_event(event)
    if not member_id:
        raise PermissionError("x-inqsi-member-id header is required")
    return member_id


def require_owner(event: Dict[str, Any]) -> str:
    expected = os.environ.get("INQSI_ADMIN_API_TOKEN", "").strip()
    if not expected:
        raise RuntimeError("INQSI_ADMIN_API_TOKEN is not configured")
    provided = normalized_headers(event).get("x-inqsi-admin-token", "")
    if provided != expected:
        raise PermissionError("valid x-inqsi-admin-token header is required")
    return normalized_headers(event).get("x-inqsi-member-id", "owner")


def put_audit(actor_member_id: str, action: str, target_type: str, target_id: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    created_at = utc_now()
    item = {
        "target_id": target_id,
        "audit_id": f"{created_at}#{action}#{uuid.uuid4().hex[:10]}",
        "actor_member_id": actor_member_id,
        "action": action,
        "target_type": target_type,
        "created_at": created_at,
        "metadata": metadata or {},
    }
    table("admin_audit_logs").put_item(Item=item)
    return item


def scan_limited(name: str, limit: int = 100) -> List[Dict[str, Any]]:
    return table(name).scan(Limit=limit).get("Items", [])


def get_member(member_id: str) -> Optional[Dict[str, Any]]:
    return table("members").get_item(Key={"member_id": member_id}).get("Item")


def public_member(member: Dict[str, Any]) -> Dict[str, Any]:
    return {key: member.get(key) for key in PUBLIC_MEMBER_FIELDS if key in member}


def handle_health() -> Dict[str, Any]:
    return response(200, {
        "ok": True,
        "service": "inqsi-backend-api",
        "time": utc_now(),
        "storage": "dynamodb",
        "maxSlipLegs": MAX_PARLAY_LEGS,
        "paymentProviderMode": "neutral",
        "socialProviders": ["facebook", "instagram", "reddit", "x", "tiktok", "youtube", "discord", "linkedin", "twitch", "snapchat", "other"],
        "routes": route_manifest(),
    })


def handle_member_register(body: Dict[str, Any]) -> Dict[str, Any]:
    email = (body.get("email") or "").strip().lower()
    if not email:
        return response(400, {"error": "email_required"})
    now = utc_now()
    member_id = body.get("memberId") or f"mem_{uuid.uuid4().hex[:16]}"
    item = {
        "member_id": member_id,
        "email": email,
        "created_at": now,
        "updated_at": now,
        "role": "MEMBER",
        "status": "TRIAL",
        "plan": body.get("plan") or "Full Access",
        "display_name": body.get("displayName"),
        "handle": body.get("handle"),
        "state": body.get("state"),
        "primary_sport": body.get("primarySport"),
        "public_profile_enabled": False,
        "public_score_enabled": False,
        "creator_ref": body.get("creatorRef"),
    }
    table("members").put_item(Item={key: value for key, value in item.items() if value is not None})
    put_audit(member_id, "MEMBER_CREATED", "member", member_id, {"email": email})
    return response(201, {"member": public_member(item), "memberId": member_id})


def handle_member_me(event: Dict[str, Any], method: str, body: Dict[str, Any]) -> Dict[str, Any]:
    member_id = require_member(event)
    existing = get_member(member_id)
    if not existing:
        return response(404, {"error": "member_not_found", "memberId": member_id})
    if method == "GET":
        subscriptions = table("subscriptions").query(KeyConditionExpression=Key("member_id").eq(member_id), Limit=10).get("Items", [])
        socials = table("social_accounts").query(KeyConditionExpression=Key("member_id").eq(member_id), Limit=50).get("Items", [])
        safe_socials = [{key: value for key, value in account.items() if "token" not in key.lower()} for account in socials]
        return response(200, {"member": existing, "subscriptions": subscriptions, "socialAccounts": safe_socials})

    allowed = {"display_name", "handle", "state", "primary_sport", "public_profile_enabled", "public_score_enabled"}
    updates = {key: body[key] for key in allowed if key in body}
    if not updates:
        return response(400, {"error": "no_supported_fields"})
    updates["updated_at"] = utc_now()
    expression = "SET " + ", ".join(f"#{key}=:{key}" for key in updates)
    table("members").update_item(
        Key={"member_id": member_id},
        UpdateExpression=expression,
        ExpressionAttributeNames={f"#{key}": key for key in updates},
        ExpressionAttributeValues={f":{key}": value for key, value in updates.items()},
    )
    put_audit(member_id, "MEMBER_UPDATED", "member", member_id, {"fields": sorted(updates.keys())})
    return response(200, {"updated": True, "memberId": member_id})


def handle_social_start(event: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
    member_id = require_member(event)
    provider = str(body.get("provider") or "").upper()
    if not provider:
        return response(400, {"error": "provider_required"})
    auth_url = os.environ.get(f"SOCIAL_{provider}_AUTH_URL")
    client_id = os.environ.get(f"SOCIAL_{provider}_CLIENT_ID")
    redirect_uri = os.environ.get("SOCIAL_OAUTH_REDIRECT_URI")
    if not auth_url or not client_id or not redirect_uri:
        return response(501, {
            "error": "social_provider_not_configured",
            "provider": provider,
            "message": "OAuth provider credentials are not configured yet. No fake connection was created.",
        })
    state = f"{member_id}:{provider}:{uuid.uuid4().hex}"
    separator = "&" if "?" in auth_url else "?"
    url = f"{auth_url}{separator}client_id={client_id}&redirect_uri={redirect_uri}&response_type=code&state={state}"
    return response(200, {"provider": provider, "authorizationUrl": url, "state": state})


def handle_social_callback() -> Dict[str, Any]:
    return response(501, {
        "error": "social_callback_not_wired",
        "message": "Provider-specific token exchange and Secrets Manager storage must be configured before callbacks are accepted.",
    })


def handle_social_revoke(event: Dict[str, Any], connection_id: str) -> Dict[str, Any]:
    member_id = require_member(event)
    now = utc_now()
    table("social_accounts").update_item(
        Key={"member_id": member_id, "connection_id": connection_id},
        UpdateExpression="SET #status=:status, revoked_at=:revoked_at, updated_at=:updated_at",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={":status": "REVOKED", ":revoked_at": now, ":updated_at": now},
    )
    put_audit(member_id, "SOCIAL_REVOKED", "social_account", connection_id, {})
    return response(200, {"revoked": True, "connectionId": connection_id})


def handle_save_slip(event: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
    member_id = require_member(event)
    legs = body.get("legs") or []
    if not isinstance(legs, list) or not legs:
        return response(400, {"error": "legs_required"})
    if len(legs) > MAX_PARLAY_LEGS:
        return response(400, {"error": "too_many_legs", "maxLegs": MAX_PARLAY_LEGS})
    now = utc_now()
    slip_id = body.get("slipId") or f"slip_{uuid.uuid4().hex[:16]}"
    normalized_legs = []
    for index, leg in enumerate(legs, start=1):
        normalized_legs.append({
            "leg_id": leg.get("legId") or f"leg_{index}",
            "game_id": leg.get("gameId") or leg.get("game_id"),
            "sport": leg.get("sport"),
            "market_type": leg.get("marketType") or leg.get("market_type"),
            "selection": leg.get("selection"),
            "book": leg.get("book"),
            "odds_american": leg.get("oddsAmerican") or leg.get("odds_american"),
            "line": leg.get("line"),
            "result": "PENDING",
            "risk_tags": leg.get("riskTags") or [],
        })
    item = {
        "member_id": member_id,
        "slip_id": slip_id,
        "created_at": now,
        "updated_at": now,
        "legs": normalized_legs,
        "status": "SAVED",
        "visibility": body.get("visibility") or "PRIVATE",
        "source": body.get("source") or "MANUAL",
    }
    table("saved_slips").put_item(Item=item)
    put_audit(member_id, "SLIP_SAVED", "slip", slip_id, {"legs": len(normalized_legs)})
    return response(201, {"saved": True, "slip": item})


def handle_list_slips(event: Dict[str, Any]) -> Dict[str, Any]:
    member_id = require_member(event)
    items = table("saved_slips").query(KeyConditionExpression=Key("member_id").eq(member_id), Limit=100).get("Items", [])
    return response(200, {"slips": items})


def handle_get_slip(event: Dict[str, Any], slip_id: str) -> Dict[str, Any]:
    member_id = require_member(event)
    item = table("saved_slips").get_item(Key={"member_id": member_id, "slip_id": slip_id}).get("Item")
    if not item:
        return response(404, {"error": "slip_not_found", "slipId": slip_id})
    return response(200, {"slip": item})


def handle_grade_slip(event: Dict[str, Any], slip_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    actor = require_owner(event)
    member_id = body.get("memberId") or body.get("member_id")
    if not member_id:
        return response(400, {"error": "memberId_required"})
    result = table("saved_slips").get_item(Key={"member_id": member_id, "slip_id": slip_id})
    item = result.get("Item")
    if not item:
        return response(404, {"error": "slip_not_found", "slipId": slip_id})
    leg_results = body.get("legResults") or {}
    wins = losses = pushes = 0
    graded_legs = []
    for leg in item.get("legs", []):
        leg_result = str(leg_results.get(leg.get("leg_id"), leg.get("result", "PENDING"))).upper()
        if leg_result == "WIN":
            wins += 1
        elif leg_result == "LOSS":
            losses += 1
        elif leg_result in ["PUSH", "VOID"]:
            pushes += 1
        graded = {**leg, "result": leg_result, "graded_at": utc_now()}
        graded_legs.append(graded)
    graded_count = wins + losses + pushes
    accuracy = (wins / graded_count) * 100 if graded_count else 0
    now = utc_now()
    table("saved_slips").update_item(
        Key={"member_id": member_id, "slip_id": slip_id},
        UpdateExpression="SET legs=:legs, #status=:status, score=:score, updated_at=:updated_at, post_game_review=:review",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={":legs": graded_legs, ":status": "GRADED", ":score": Decimal(str(round(accuracy, 2))), ":updated_at": now, ":review": body.get("postGameReview") or "Manual grade stored."},
    )
    score_item = {
        "member_id": member_id,
        "score_id": f"score_{now}#{slip_id}",
        "window": "LIFETIME",
        "calculated_at": now,
        "total_slips": 1,
        "graded_slips": 1,
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "accuracy_pct": Decimal(str(round(accuracy, 2))),
        "public_visible": False,
        "public_visible_key": "false",
    }
    table("score_history").put_item(Item=score_item)
    put_audit(actor, "SLIP_GRADED", "slip", slip_id, {"memberId": member_id, "accuracy": accuracy})
    return response(200, {"graded": True, "score": to_json_safe(score_item)})


def handle_scores_me(event: Dict[str, Any]) -> Dict[str, Any]:
    member_id = require_member(event)
    items = table("score_history").query(KeyConditionExpression=Key("member_id").eq(member_id), Limit=100).get("Items", [])
    return response(200, {"scores": items})


def handle_public_member(handle: str) -> Dict[str, Any]:
    result = table("members").query(IndexName="HandleIndex", KeyConditionExpression=Key("handle").eq(handle), Limit=1)
    items = result.get("Items", [])
    if not items:
        return response(404, {"error": "public_member_not_found"})
    member = items[0]
    if not member.get("public_score_enabled"):
        return response(403, {"error": "public_score_not_enabled"})
    scores = table("score_history").query(KeyConditionExpression=Key("member_id").eq(member["member_id"]), Limit=25).get("Items", [])
    return response(200, {"member": public_member(member), "scores": scores})


def handle_attribution_visit(body: Dict[str, Any]) -> Dict[str, Any]:
    creator_ref = body.get("creatorRef") or body.get("creator_ref")
    if not creator_ref:
        return response(400, {"error": "creatorRef_required"})
    now = utc_now()
    item = {
        "attribution_id": f"attr_{uuid.uuid4().hex[:16]}",
        "anonymous_id": body.get("anonymousId") or body.get("anonymous_id") or f"anon_{uuid.uuid4().hex[:12]}",
        "member_id": body.get("memberId") or body.get("member_id"),
        "creator_ref": creator_ref,
        "touch": body.get("touch") or "FIRST",
        "first_seen_at": now,
        "last_seen_at": now,
        "landing_path": body.get("landingPath"),
        "utm_source": body.get("utmSource"),
        "utm_medium": body.get("utmMedium"),
        "utm_campaign": body.get("utmCampaign"),
    }
    table("creator_attribution").put_item(Item={key: value for key, value in item.items() if value is not None})
    return response(201, {"stored": True, "attribution": item})


def handle_attribution_convert(event: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
    member_id = require_member(event)
    creator_ref = body.get("creatorRef") or body.get("creator_ref")
    if not creator_ref:
        return response(400, {"error": "creatorRef_required"})
    now = utc_now()
    item = {
        "attribution_id": f"attr_{uuid.uuid4().hex[:16]}",
        "member_id": member_id,
        "anonymous_id": body.get("anonymousId") or body.get("anonymous_id"),
        "creator_ref": creator_ref,
        "touch": "LAST",
        "first_seen_at": body.get("firstSeenAt") or now,
        "last_seen_at": now,
        "converted_at": now,
    }
    table("creator_attribution").put_item(Item={key: value for key, value in item.items() if value is not None})
    table("members").update_item(Key={"member_id": member_id}, UpdateExpression="SET creator_ref=:creator_ref, updated_at=:updated_at", ExpressionAttributeValues={":creator_ref": creator_ref, ":updated_at": now})
    put_audit(member_id, "CREATOR_ATTRIBUTED", "member", member_id, {"creatorRef": creator_ref})
    return response(201, {"stored": True, "attribution": item})


def handle_subscription_checkout(event: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
    member_id = require_member(event)
    provider = os.environ.get("PAYMENT_PROVIDER", "NONE").upper()
    if provider in ["", "NONE"]:
        return response(501, {
            "error": "payment_provider_not_configured",
            "message": "Subscriptions are payment-provider neutral. Configure PAYMENT_PROVIDER before checkout is enabled.",
        })
    now = utc_now()
    item = {
        "member_id": member_id,
        "subscription_id": f"sub_{uuid.uuid4().hex[:16]}",
        "provider": provider,
        "status": "INCOMPLETE",
        "plan": body.get("plan") or "Full Access",
        "created_at": now,
        "updated_at": now,
    }
    table("subscriptions").put_item(Item=item)
    put_audit(member_id, "SUBSCRIPTION_UPDATED", "subscription", item["subscription_id"], {"provider": provider})
    return response(202, {"created": True, "subscription": item, "message": "Provider-specific checkout URL is not wired yet."})


def handle_subscription_webhook(body: Dict[str, Any]) -> Dict[str, Any]:
    provider = os.environ.get("PAYMENT_PROVIDER", "NONE").upper()
    if provider in ["", "NONE"]:
        return response(501, {"error": "payment_provider_not_configured"})
    return response(501, {"error": "provider_webhook_not_implemented", "provider": provider, "received": bool(body)})


def handle_admin_dashboard(event: Dict[str, Any]) -> Dict[str, Any]:
    actor = require_owner(event)
    data = {name: scan_limited(name, 50) for name in ["members", "saved_slips", "score_history", "creator_attribution", "subscriptions", "support_notes", "feature_flags"]}
    put_audit(actor, "ADMIN_VIEWED", "admin", "dashboard", {})
    return response(200, {"dashboard": data})


def handle_admin_members(event: Dict[str, Any]) -> Dict[str, Any]:
    actor = require_owner(event)
    put_audit(actor, "ADMIN_VIEWED", "admin", "members", {})
    return response(200, {"members": scan_limited("members", 100)})


def handle_admin_social(event: Dict[str, Any]) -> Dict[str, Any]:
    actor = require_owner(event)
    accounts = scan_limited("social_accounts", 100)
    safe = [{key: value for key, value in account.items() if "token" not in key.lower()} for account in accounts]
    put_audit(actor, "ADMIN_VIEWED", "admin", "social_accounts", {})
    return response(200, {"socialAccounts": safe})


def handle_admin_audit(event: Dict[str, Any]) -> Dict[str, Any]:
    require_owner(event)
    return response(200, {"auditEvents": scan_limited("admin_audit_logs", 100)})


def handle_feature_flag(event: Dict[str, Any], flag_key: str, body: Dict[str, Any]) -> Dict[str, Any]:
    actor = require_owner(event)
    now = utc_now()
    item = {
        "flag_key": flag_key,
        "enabled": bool(body.get("enabled")),
        "updated_at": now,
        "updated_by": actor,
        "description": body.get("description") or "",
    }
    table("feature_flags").put_item(Item=item)
    put_audit(actor, "FEATURE_FLAG_UPDATED", "feature_flag", flag_key, {"enabled": item["enabled"]})
    return response(200, {"featureFlag": item})


def dispatch(event: Dict[str, Any]) -> Dict[str, Any]:
    method, path = method_and_path(event)
    body = parse_body(event)
    if method == "OPTIONS":
        return response(200, {"ok": True})
    if path == "/v1/health":
        return handle_health()
    if path == "/v1/members/register" and method == "POST":
        return handle_member_register(body)
    if path == "/v1/members/me" and method in ["GET", "PATCH"]:
        return handle_member_me(event, method, body)
    if path == "/v1/members/social/connect/start" and method == "POST":
        return handle_social_start(event, body)
    if path == "/v1/members/social/callback" and method == "GET":
        return handle_social_callback()
    if path.startswith("/v1/members/social/") and method == "DELETE":
        return handle_social_revoke(event, path.split("/")[-1])
    if path == "/v1/slips" and method == "POST":
        return handle_save_slip(event, body)
    if path == "/v1/slips" and method == "GET":
        return handle_list_slips(event)
    if path.startswith("/v1/slips/"):
        parts = path.split("/")
        slip_id = parts[3] if len(parts) > 3 else ""
        if len(parts) == 5 and parts[4] == "grade" and method == "POST":
            return handle_grade_slip(event, slip_id, body)
        if method == "GET":
            return handle_get_slip(event, slip_id)
    if path == "/v1/scores/me" and method == "GET":
        return handle_scores_me(event)
    if path.startswith("/v1/public/u/") and method == "GET":
        return handle_public_member(path.split("/")[-1])
    if path == "/v1/attribution/visit" and method == "POST":
        return handle_attribution_visit(body)
    if path == "/v1/attribution/convert" and method == "POST":
        return handle_attribution_convert(event, body)
    if path == "/v1/subscriptions/checkout" and method == "POST":
        return handle_subscription_checkout(event, body)
    if path == "/v1/subscriptions/webhook" and method == "POST":
        return handle_subscription_webhook(body)
    if path == "/v1/admin/dashboard" and method == "GET":
        return handle_admin_dashboard(event)
    if path == "/v1/admin/members" and method == "GET":
        return handle_admin_members(event)
    if path == "/v1/admin/social-accounts" and method == "GET":
        return handle_admin_social(event)
    if path == "/v1/admin/audit" and method == "GET":
        return handle_admin_audit(event)
    if path.startswith("/v1/admin/feature-flags/") and method == "PATCH":
        return handle_feature_flag(event, path.split("/")[-1], body)
    return response(404, {"error": "not_found", "path": path})


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    try:
        return dispatch(event)
    except PermissionError as exc:
        return response(401, {"error": "unauthorized", "message": str(exc)})
    except RuntimeError as exc:
        return response(503, {"error": "backend_not_wired", "message": str(exc)})
    except Exception as exc:
        return response(500, {"error": "backend_error", "message": str(exc)})
