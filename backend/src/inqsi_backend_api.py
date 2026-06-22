"""Deployable InQsi backend API foundation.

This Lambda exposes real storage-backed member, slip, score, attribution,
subscription, social account, admin, scanner, builder, grading, and monitoring
routes. It intentionally refuses to pretend a provider or odds feed is wired
when live market data is missing.
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

ACTIVE_MEMBER_STATUSES = {"TRIAL", "ACTIVE"}
ACTIVE_SUBSCRIPTION_STATUSES = {"TRIALING", "ACTIVE"}
OWNER_ROLES = {"ADMIN", "OWNER"}
SUPPORTED_SPORTS = {"NFL", "CFB", "NBA", "NCAAM", "NHL", "MLB", "WNBA", "SOCCER", "TENNIS", "MMA", "BOXING", "GOLF", "ESPORTS"}


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
    return {"statusCode": status_code, "headers": CORS_HEADERS, "body": json.dumps(to_json_safe(body))}


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


def get_access_state(member_id: str) -> Dict[str, Any]:
    member = get_member(member_id)
    if not member:
        return {"allowed": False, "reason": "member_not_found", "status": "UNKNOWN"}
    role = str(member.get("role") or "MEMBER").upper()
    status = str(member.get("status") or "").upper()
    if role in OWNER_ROLES or status in ACTIVE_MEMBER_STATUSES:
        return {"allowed": True, "reason": "member_status", "status": status, "role": role, "member": public_member(member)}
    subscriptions = table("subscriptions").query(KeyConditionExpression=Key("member_id").eq(member_id), Limit=25).get("Items", [])
    active = [sub for sub in subscriptions if str(sub.get("status") or "").upper() in ACTIVE_SUBSCRIPTION_STATUSES]
    if active:
        return {"allowed": True, "reason": "subscription_status", "status": status, "role": role, "subscription": active[0], "member": public_member(member)}
    return {"allowed": False, "reason": "subscription_required", "status": status, "role": role, "member": public_member(member)}


def require_access(event: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    member_id = require_member(event)
    access = get_access_state(member_id)
    if not access.get("allowed"):
        raise PermissionError(f"paywall_required:{access.get('reason')}")
    return member_id, access


def normalize_leg(leg: Dict[str, Any], index: int) -> Dict[str, Any]:
    return {
        "leg_id": leg.get("legId") or leg.get("leg_id") or f"leg_{index}",
        "game_id": leg.get("gameId") or leg.get("game_id"),
        "sport": str(leg.get("sport") or "").upper() or None,
        "market_type": leg.get("marketType") or leg.get("market_type"),
        "selection": leg.get("selection"),
        "book": leg.get("book"),
        "odds_american": leg.get("oddsAmerican") or leg.get("odds_american"),
        "line": leg.get("line"),
        "result": leg.get("result") or "PENDING",
        "risk_tags": leg.get("riskTags") or leg.get("risk_tags") or [],
        "market_snapshots": leg.get("marketSnapshots") or leg.get("market_snapshots") or [],
    }


def validate_legs(legs: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(legs, list) or not legs:
        return {"error": "legs_required"}
    if len(legs) > MAX_PARLAY_LEGS:
        return {"error": "too_many_legs", "maxLegs": MAX_PARLAY_LEGS}
    for index, leg in enumerate(legs, start=1):
        if not isinstance(leg, dict):
            return {"error": "invalid_leg", "legIndex": index}
        if str(leg.get("sport") or "").upper() not in SUPPORTED_SPORTS:
            return {"error": "unsupported_or_missing_sport", "legIndex": index, "supportedSports": sorted(SUPPORTED_SPORTS)}
        if not (leg.get("selection") and (leg.get("marketType") or leg.get("market_type"))):
            return {"error": "selection_and_market_type_required", "legIndex": index}
    return None


def score_leg_without_odds_api(normalized_leg: Dict[str, Any]) -> Dict[str, Any]:
    snapshots = normalized_leg.get("market_snapshots") or []
    tags = list(normalized_leg.get("risk_tags") or [])
    if not snapshots:
        return {
            "legId": normalized_leg["leg_id"],
            "selection": normalized_leg.get("selection"),
            "riskLevel": "UNAVAILABLE",
            "confidenceBand": "DATA_REQUIRED",
            "tags": sorted(set(tags + ["MARKET_DATA_REQUIRED"])),
            "score": None,
            "message": "Verified market snapshots are required before InQsi can score this leg.",
        }

    signal_tags = []
    movement_points = []
    for snap in snapshots:
        if isinstance(snap, dict):
            signal_tags.extend(snap.get("signalTags") or snap.get("signal_tags") or [])
            value = snap.get("impliedProbability") or snap.get("implied_probability") or snap.get("priceDelta") or snap.get("price_delta")
            if isinstance(value, (int, float)):
                movement_points.append(float(value))
    all_tags = sorted(set(tags + signal_tags))
    score = 50
    if "STEAM" in all_tags or "CERTAINTY_ANCHOR" in all_tags:
        score += 18
    if "RESISTANCE" in all_tags:
        score -= 15
    if "REVERSAL" in all_tags or "TRAP" in all_tags or "CHAOS" in all_tags:
        score -= 22
    if len(snapshots) < 2:
        all_tags.append("INSUFFICIENT_T_SNAPSHOTS")
        score -= 12
    score = max(0, min(100, score))
    risk_level = "LOW" if score >= 70 else "MEDIUM" if score >= 45 else "HIGH"
    confidence = "HIGH" if score >= 75 else "MODERATE" if score >= 55 else "FRAGILE"
    return {
        "legId": normalized_leg["leg_id"],
        "selection": normalized_leg.get("selection"),
        "riskLevel": risk_level,
        "confidenceBand": confidence,
        "tags": sorted(set(all_tags)),
        "score": Decimal(str(score)),
        "snapshotCount": len(snapshots),
        "message": "Scored from caller-supplied verified market snapshots.",
    }


def build_scan(member_id: str, legs: List[Dict[str, Any]], source_slip_id: Optional[str] = None) -> Dict[str, Any]:
    normalized = [normalize_leg(leg, index) for index, leg in enumerate(legs, start=1)]
    leg_reads = [score_leg_without_odds_api(leg) for leg in normalized]
    unavailable = [leg for leg in leg_reads if leg.get("confidenceBand") == "DATA_REQUIRED"]
    scored = [leg for leg in leg_reads if isinstance(leg.get("score"), Decimal)]
    overall_score = None if unavailable or not scored else Decimal(str(round(sum(float(leg["score"]) for leg in scored) / len(scored), 2)))
    weakest = None
    if scored:
        weakest = min(scored, key=lambda leg: float(leg["score"]))
    overall_read = "DATA_REQUIRED" if unavailable else "CLEAR" if overall_score and overall_score >= 70 else "CAUTION"
    if scored and any(leg.get("riskLevel") == "HIGH" for leg in scored):
        overall_read = "DO_NOT_FORCE"
    return {
        "scanId": f"scan_{uuid.uuid4().hex[:16]}",
        "memberId": member_id,
        "sourceSlipId": source_slip_id,
        "createdAt": utc_now(),
        "overallRead": overall_read,
        "overallScore": overall_score,
        "legReads": leg_reads,
        "weakestLeg": weakest,
        "dataStatus": "MARKET_DATA_REQUIRED" if unavailable else "SCANNED_FROM_VERIFIED_INPUT",
        "notes": [
            "No fake scores were created.",
            "Odds API ingestion is not required for this endpoint when verified market snapshots are supplied in the request.",
        ],
    }


def append_scan_history(member_id: str, slip_id: str, scan: Dict[str, Any]) -> None:
    table("saved_slips").update_item(
        Key={"member_id": member_id, "slip_id": slip_id},
        UpdateExpression="SET scan_history=list_append(if_not_exists(scan_history, :empty), :scan), last_scan=:last_scan, updated_at=:updated_at",
        ExpressionAttributeValues={":empty": [], ":scan": [scan], ":last_scan": scan, ":updated_at": utc_now()},
    )


def handle_health() -> Dict[str, Any]:
    return response(200, {
        "ok": True,
        "service": "inqsi-backend-api",
        "time": utc_now(),
        "storage": "dynamodb",
        "maxSlipLegs": MAX_PARLAY_LEGS,
        "paymentProviderMode": os.environ.get("PAYMENT_PROVIDER", "NONE"),
        "scannerReadyWithoutOddsApi": True,
        "oddsApiDependentItemsSkipped": ["odds ingestion", "multi-book normalization", "scheduled T snapshots"],
        "socialProviders": ["facebook", "instagram", "reddit", "x", "tiktok", "youtube", "discord", "linkedin", "twitch", "snapchat", "other"],
        "routes": route_manifest(),
        "productRoutes": ["/v1/scanner/scan", "/v1/slips/{slipId}/scan", "/v1/slips/{slipId}/scan-history", "/v1/parlays/build", "/v1/subscriptions/access", "/v1/results/grade", "/v1/monitoring/data-quality"],
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
        return response(200, {"member": existing, "subscriptions": subscriptions, "socialAccounts": safe_socials, "access": get_access_state(member_id)})
    allowed = {"display_name", "handle", "state", "primary_sport", "public_profile_enabled", "public_score_enabled"}
    updates = {key: body[key] for key in allowed if key in body}
    if not updates:
        return response(400, {"error": "no_supported_fields"})
    updates["updated_at"] = utc_now()
    expression = "SET " + ", ".join(f"#{key}=:{key}" for key in updates)
    table("members").update_item(Key={"member_id": member_id}, UpdateExpression=expression, ExpressionAttributeNames={f"#{key}": key for key in updates}, ExpressionAttributeValues={f":{key}": value for key, value in updates.items()})
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
        return response(501, {"error": "social_provider_not_configured", "provider": provider, "message": "OAuth provider credentials are not configured yet. No fake connection was created."})
    state = f"{member_id}:{provider}:{uuid.uuid4().hex}"
    separator = "&" if "?" in auth_url else "?"
    url = f"{auth_url}{separator}client_id={client_id}&redirect_uri={redirect_uri}&response_type=code&state={state}"
    return response(200, {"provider": provider, "authorizationUrl": url, "state": state})


def handle_social_callback() -> Dict[str, Any]:
    return response(501, {"error": "social_callback_not_wired", "message": "Provider-specific token exchange and Secrets Manager storage must be configured before callbacks are accepted."})


def handle_social_revoke(event: Dict[str, Any], connection_id: str) -> Dict[str, Any]:
    member_id = require_member(event)
    now = utc_now()
    table("social_accounts").update_item(Key={"member_id": member_id, "connection_id": connection_id}, UpdateExpression="SET #status=:status, revoked_at=:revoked_at, updated_at=:updated_at", ExpressionAttributeNames={"#status": "status"}, ExpressionAttributeValues={":status": "REVOKED", ":revoked_at": now, ":updated_at": now})
    put_audit(member_id, "SOCIAL_REVOKED", "social_account", connection_id, {})
    return response(200, {"revoked": True, "connectionId": connection_id})


def write_slip(member_id: str, body: Dict[str, Any], source: str = "MANUAL", scan: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    legs = body.get("legs") or []
    validation = validate_legs(legs)
    if validation:
        raise ValueError(json.dumps(validation))
    now = utc_now()
    slip_id = body.get("slipId") or f"slip_{uuid.uuid4().hex[:16]}"
    normalized_legs = []
    for index, leg in enumerate(legs, start=1):
        normalized = normalize_leg(leg, index)
        normalized.pop("market_snapshots", None)
        normalized_legs.append(normalized)
    item = {
        "member_id": member_id,
        "slip_id": slip_id,
        "created_at": now,
        "updated_at": now,
        "legs": normalized_legs,
        "status": "SAVED",
        "visibility": body.get("visibility") or "PRIVATE",
        "source": source,
    }
    if scan:
        item["scan_history"] = [scan]
        item["last_scan"] = scan
    table("saved_slips").put_item(Item=item)
    put_audit(member_id, "SLIP_SAVED", "slip", slip_id, {"legs": len(normalized_legs), "source": source})
    return item


def handle_save_slip(event: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
    member_id = require_member(event)
    try:
        item = write_slip(member_id, body)
    except ValueError as exc:
        return response(400, json.loads(str(exc)))
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


def handle_scanner_scan(event: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
    member_id, access = require_access(event)
    legs = body.get("legs") or []
    validation = validate_legs(legs)
    if validation:
        return response(400, validation)
    scan = build_scan(member_id, legs)
    saved_slip = None
    if body.get("save") is True:
        saved_slip = write_slip(member_id, body, source="AI_SLIP_SCANNER", scan=scan)
        scan["sourceSlipId"] = saved_slip["slip_id"]
    return response(200, {"scan": scan, "savedSlip": saved_slip, "access": {"allowed": access.get("allowed"), "reason": access.get("reason")}})


def handle_scan_saved_slip(event: Dict[str, Any], slip_id: str) -> Dict[str, Any]:
    member_id, access = require_access(event)
    item = table("saved_slips").get_item(Key={"member_id": member_id, "slip_id": slip_id}).get("Item")
    if not item:
        return response(404, {"error": "slip_not_found", "slipId": slip_id})
    scan = build_scan(member_id, item.get("legs", []), source_slip_id=slip_id)
    append_scan_history(member_id, slip_id, scan)
    put_audit(member_id, "SLIP_SCANNED", "slip", slip_id, {"overallRead": scan["overallRead"], "dataStatus": scan["dataStatus"]})
    return response(200, {"scan": scan, "access": {"allowed": access.get("allowed"), "reason": access.get("reason")}})


def handle_scan_history(event: Dict[str, Any], slip_id: str) -> Dict[str, Any]:
    member_id = require_member(event)
    item = table("saved_slips").get_item(Key={"member_id": member_id, "slip_id": slip_id}).get("Item")
    if not item:
        return response(404, {"error": "slip_not_found", "slipId": slip_id})
    return response(200, {"slipId": slip_id, "scanHistory": item.get("scan_history", []), "lastScan": item.get("last_scan")})


def handle_parlay_build(event: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
    member_id, access = require_access(event)
    candidates = body.get("candidates") or []
    if not isinstance(candidates, list) or not candidates:
        return response(424, {"error": "market_candidates_required", "message": "Parlay builder requires verified candidate legs from market data. No odds-only or fake build was created."})
    valid_candidates = []
    for index, candidate in enumerate(candidates, start=1):
        if not isinstance(candidate, dict):
            continue
        normalized = normalize_leg(candidate, index)
        read = score_leg_without_odds_api(normalized)
        if read.get("confidenceBand") != "DATA_REQUIRED" and read.get("riskLevel") != "HIGH":
            valid_candidates.append({"leg": normalized, "read": read})
    if len(valid_candidates) < MAX_PARLAY_LEGS:
        return response(200, {"buildStatus": "NO_BUILD", "reason": "not_enough_verified_low_or_medium_risk_candidates", "candidateCount": len(candidates), "eligibleCount": len(valid_candidates), "message": "InQsi refused to force a parlay."})
    ranked = sorted(valid_candidates, key=lambda item: float(item["read"].get("score") or 0), reverse=True)[:MAX_PARLAY_LEGS]
    parlay = {"parlayId": f"parlay_{uuid.uuid4().hex[:16]}", "memberId": member_id, "createdAt": utc_now(), "legs": [item["leg"] for item in ranked], "legReads": [item["read"] for item in ranked], "structure": "VERIFIED_INPUT_ONLY", "warning": "Builder used only caller-supplied verified candidates."}
    return response(200, {"buildStatus": "BUILT", "parlay": parlay, "access": {"allowed": access.get("allowed"), "reason": access.get("reason")}})


def handle_grade_slip(event: Dict[str, Any], slip_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    actor = require_owner(event)
    member_id = body.get("memberId") or body.get("member_id")
    if not member_id:
        return response(400, {"error": "memberId_required"})
    item = table("saved_slips").get_item(Key={"member_id": member_id, "slip_id": slip_id}).get("Item")
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
        graded_legs.append({**leg, "result": leg_result, "graded_at": utc_now()})
    graded_count = wins + losses + pushes
    accuracy = (wins / graded_count) * 100 if graded_count else 0
    now = utc_now()
    learning = {
        "created_at": now,
        "missed_legs": [leg for leg in graded_legs if leg.get("result") == "LOSS"],
        "risk_tags_before_result": sorted({tag for leg in item.get("legs", []) for tag in leg.get("risk_tags", [])}),
        "note": body.get("postGameReview") or "Manual grade stored. Learning loop ready for automated result feed when provider is connected.",
    }
    table("saved_slips").update_item(Key={"member_id": member_id, "slip_id": slip_id}, UpdateExpression="SET legs=:legs, #status=:status, score=:score, updated_at=:updated_at, post_game_review=:review, learning_feedback=:learning", ExpressionAttributeNames={"#status": "status"}, ExpressionAttributeValues={":legs": graded_legs, ":status": "GRADED", ":score": Decimal(str(round(accuracy, 2))), ":updated_at": now, ":review": learning["note"], ":learning": learning})
    score_item = {"member_id": member_id, "score_id": f"score_{now}#{slip_id}", "window": "LIFETIME", "calculated_at": now, "total_slips": 1, "graded_slips": 1, "wins": wins, "losses": losses, "pushes": pushes, "accuracy_pct": Decimal(str(round(accuracy, 2))), "public_visible": False, "public_visible_key": "false", "learning_feedback": learning}
    table("score_history").put_item(Item=score_item)
    put_audit(actor, "SLIP_GRADED", "slip", slip_id, {"memberId": member_id, "accuracy": accuracy, "learningLoop": True})
    return response(200, {"graded": True, "score": score_item, "learningFeedback": learning})


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
    item = {"attribution_id": f"attr_{uuid.uuid4().hex[:16]}", "anonymous_id": body.get("anonymousId") or body.get("anonymous_id") or f"anon_{uuid.uuid4().hex[:12]}", "member_id": body.get("memberId") or body.get("member_id"), "creator_ref": creator_ref, "touch": body.get("touch") or "FIRST", "first_seen_at": now, "last_seen_at": now, "landing_path": body.get("landingPath"), "utm_source": body.get("utmSource"), "utm_medium": body.get("utmMedium"), "utm_campaign": body.get("utmCampaign")}
    table("creator_attribution").put_item(Item={key: value for key, value in item.items() if value is not None})
    return response(201, {"stored": True, "attribution": item})


def handle_attribution_convert(event: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
    member_id = require_member(event)
    creator_ref = body.get("creatorRef") or body.get("creator_ref")
    if not creator_ref:
        return response(400, {"error": "creatorRef_required"})
    now = utc_now()
    item = {"attribution_id": f"attr_{uuid.uuid4().hex[:16]}", "member_id": member_id, "anonymous_id": body.get("anonymousId") or body.get("anonymous_id"), "creator_ref": creator_ref, "touch": "LAST", "first_seen_at": body.get("firstSeenAt") or now, "last_seen_at": now, "converted_at": now}
    table("creator_attribution").put_item(Item={key: value for key, value in item.items() if value is not None})
    table("members").update_item(Key={"member_id": member_id}, UpdateExpression="SET creator_ref=:creator_ref, updated_at=:updated_at", ExpressionAttributeValues={":creator_ref": creator_ref, ":updated_at": now})
    put_audit(member_id, "CREATOR_ATTRIBUTED", "member", member_id, {"creatorRef": creator_ref})
    return response(201, {"stored": True, "attribution": item})


def handle_subscription_access(event: Dict[str, Any]) -> Dict[str, Any]:
    member_id = require_member(event)
    return response(200, {"access": get_access_state(member_id)})


def handle_subscription_checkout(event: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
    member_id = require_member(event)
    provider = os.environ.get("PAYMENT_PROVIDER", "NONE").upper()
    if provider in ["", "NONE"]:
        return response(501, {"error": "payment_provider_not_configured", "message": "Subscriptions are payment-provider neutral. Configure PAYMENT_PROVIDER before checkout is enabled."})
    now = utc_now()
    item = {"member_id": member_id, "subscription_id": f"sub_{uuid.uuid4().hex[:16]}", "provider": provider, "status": "INCOMPLETE", "plan": body.get("plan") or "Full Access", "created_at": now, "updated_at": now}
    table("subscriptions").put_item(Item=item)
    put_audit(member_id, "SUBSCRIPTION_UPDATED", "subscription", item["subscription_id"], {"provider": provider})
    return response(202, {"created": True, "subscription": item, "message": "Provider-specific checkout URL is not wired yet."})


def handle_subscription_webhook(body: Dict[str, Any]) -> Dict[str, Any]:
    provider = os.environ.get("PAYMENT_PROVIDER", "NONE").upper()
    if provider in ["", "NONE"]:
        return response(501, {"error": "payment_provider_not_configured"})
    return response(501, {"error": "provider_webhook_not_implemented", "provider": provider, "received": bool(body)})


def handle_monitoring_data_quality(event: Dict[str, Any]) -> Dict[str, Any]:
    actor = require_owner(event)
    slips = scan_limited("saved_slips", 200)
    scores = scan_limited("score_history", 200)
    subscriptions = scan_limited("subscriptions", 200)
    issues = []
    for slip in slips:
        legs = slip.get("legs", [])
        if len(legs) > MAX_PARLAY_LEGS:
            issues.append({"severity": "FAIL", "type": "too_many_legs", "slipId": slip.get("slip_id")})
        if slip.get("source") in ["AI_SLIP_SCANNER", "AI_SLIP_BUILDER"] and not slip.get("scan_history"):
            issues.append({"severity": "WARN", "type": "missing_scan_history", "slipId": slip.get("slip_id")})
        if slip.get("status") == "SAVED" and not slip.get("last_scan"):
            issues.append({"severity": "WARN", "type": "saved_without_scan", "slipId": slip.get("slip_id")})
    incomplete_subs = [sub for sub in subscriptions if str(sub.get("status") or "").upper() in ["INCOMPLETE", "PAST_DUE"]]
    if incomplete_subs:
        issues.append({"severity": "WARN", "type": "incomplete_or_past_due_subscriptions", "count": len(incomplete_subs)})
    status = "FAIL" if any(issue["severity"] == "FAIL" for issue in issues) else "WARN" if issues else "PASS"
    report = {"status": status, "checkedAt": utc_now(), "slipsChecked": len(slips), "scoresChecked": len(scores), "subscriptionsChecked": len(subscriptions), "issues": issues, "oddsApiDependentChecksSkipped": ["provider freshness", "multi-book completeness", "T1-T5 snapshot coverage"]}
    put_audit(actor, "DATA_QUALITY_CHECKED", "monitoring", "data-quality", {"status": status, "issueCount": len(issues)})
    return response(200, report)


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
    item = {"flag_key": flag_key, "enabled": bool(body.get("enabled")), "updated_at": now, "updated_by": actor, "description": body.get("description") or ""}
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
    if path == "/v1/scanner/scan" and method == "POST":
        return handle_scanner_scan(event, body)
    if path == "/v1/parlays/build" and method == "POST":
        return handle_parlay_build(event, body)
    if path == "/v1/subscriptions/access" and method == "GET":
        return handle_subscription_access(event)
    if path == "/v1/results/grade" and method == "POST":
        return handle_grade_slip(event, body.get("slipId") or body.get("slip_id") or "", body)
    if path == "/v1/monitoring/data-quality" and method == "GET":
        return handle_monitoring_data_quality(event)
    if path == "/v1/slips" and method == "POST":
        return handle_save_slip(event, body)
    if path == "/v1/slips" and method == "GET":
        return handle_list_slips(event)
    if path.startswith("/v1/slips/"):
        parts = path.split("/")
        slip_id = parts[3] if len(parts) > 3 else ""
        if len(parts) == 5 and parts[4] == "grade" and method == "POST":
            return handle_grade_slip(event, slip_id, body)
        if len(parts) == 5 and parts[4] == "scan" and method == "POST":
            return handle_scan_saved_slip(event, slip_id)
        if len(parts) == 5 and parts[4] == "scan-history" and method == "GET":
            return handle_scan_history(event, slip_id)
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
        message = str(exc)
        if message.startswith("paywall_required:"):
            return response(402, {"error": "paywall_required", "reason": message.split(":", 1)[-1]})
        return response(401, {"error": "unauthorized", "message": message})
    except RuntimeError as exc:
        return response(503, {"error": "backend_not_wired", "message": str(exc)})
    except Exception as exc:
        return response(500, {"error": "backend_error", "message": str(exc)})
