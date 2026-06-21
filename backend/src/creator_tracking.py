import json
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict

import boto3
from boto3.dynamodb.conditions import Key

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "content-type,authorization,x-inqsi-signature",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
}

DDB = boto3.resource("dynamodb")
CREATORS_TABLE = DDB.Table(os.environ["CREATORS_TABLE"])
ATTRIBUTION_EVENTS_TABLE = DDB.Table(os.environ["ATTRIBUTION_EVENTS_TABLE"])
USER_ATTRIBUTION_TABLE = DDB.Table(os.environ["USER_ATTRIBUTION_TABLE"])
MEMBERSHIP_TABLE = DDB.Table(os.environ["MEMBERSHIP_TABLE"])

LIVE_PAID = "live_paid"
VALID_STATUSES = {"trial", "live_paid", "past_due", "canceled", "expired", "refunded"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def safe_json(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    if isinstance(value, list):
        return [safe_json(item) for item in value]
    if isinstance(value, dict):
        return {key: safe_json(item) for key, item in value.items()}
    return value


def safe_decimal(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, list):
        return [safe_decimal(item) for item in value]
    if isinstance(value, dict):
        return {key: safe_decimal(item) for key, item in value.items()}
    return value


def response(status_code: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {"statusCode": status_code, "headers": CORS_HEADERS, "body": json.dumps(safe_json(body))}


def body(event: Dict[str, Any]) -> Dict[str, Any]:
    raw = event.get("body")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def code(value: Any) -> str:
    return "".join(ch for ch in str(value or "").strip().lower() if ch.isalnum() or ch in ["-", "_"])


def creator_by_code(value: Any):
    referral_code = code(value)
    if not referral_code:
        return None
    result = CREATORS_TABLE.query(IndexName="ReferralCodeIndex", KeyConditionExpression=Key("referral_code").eq(referral_code), Limit=1)
    items = result.get("Items", [])
    return items[0] if items else None


def create_creator(payload: Dict[str, Any]) -> Dict[str, Any]:
    referral_code = code(payload.get("referralCode") or payload.get("referral_code"))
    if not referral_code:
        return response(400, {"error": "referral_code_required"})
    if creator_by_code(referral_code):
        return response(409, {"error": "referral_code_exists"})
    now = utc_now()
    item = {
        "creator_id": payload.get("creatorId") or payload.get("creator_id") or f"creator_{uuid.uuid4().hex[:12]}",
        "creator_name": payload.get("creatorName") or payload.get("creator_name") or referral_code,
        "handle": payload.get("handle"),
        "referral_code": referral_code,
        "campaign_name": payload.get("campaignName") or payload.get("campaign_name") or "default",
        "commission_type": payload.get("commissionType") or payload.get("commission_type") or "manual",
        "commission_amount": payload.get("commissionAmount") or payload.get("commission_amount") or 0,
        "active": bool(payload.get("active", True)),
        "created_at": now,
        "updated_at": now,
    }
    CREATORS_TABLE.put_item(Item=safe_decimal(item))
    return response(201, {"created": True, "creator": item})


def capture(payload: Dict[str, Any]) -> Dict[str, Any]:
    creator = creator_by_code(payload.get("promoCode") or payload.get("promo_code") or payload.get("referralCode") or payload.get("referral_code") or payload.get("ref"))
    if not creator:
        return response(404, {"error": "creator_not_found"})
    if creator.get("active") is False:
        return response(403, {"error": "creator_inactive"})
    now = utc_now()
    event = {
        "event_id": f"attr_{uuid.uuid4().hex}",
        "creator_id": creator["creator_id"],
        "referral_code": creator.get("referral_code"),
        "visitor_id": payload.get("visitorId") or payload.get("visitor_id") or f"visitor_{uuid.uuid4().hex[:12]}",
        "landing_page": payload.get("landingPage") or payload.get("landing_page"),
        "utm_source": payload.get("utm_source"),
        "utm_campaign": payload.get("utm_campaign"),
        "utm_medium": payload.get("utm_medium"),
        "created_at": now,
    }
    ATTRIBUTION_EVENTS_TABLE.put_item(Item=safe_decimal(event))
    return response(200, {"captured": True, "attribution": event})


def link_user(payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id = payload.get("userId") or payload.get("user_id")
    if not user_id:
        return response(400, {"error": "user_id_required"})
    creator = creator_by_code(payload.get("promoCode") or payload.get("promo_code") or payload.get("referralCode") or payload.get("referral_code"))
    if not creator:
        return response(404, {"error": "creator_not_found"})
    existing = USER_ATTRIBUTION_TABLE.get_item(Key={"user_id": user_id}).get("Item")
    if existing and existing.get("locked"):
        return response(200, {"linked": False, "locked": True, "attribution": existing})
    now = utc_now()
    item = {
        "user_id": user_id,
        "creator_id": creator["creator_id"],
        "referral_code": creator.get("referral_code"),
        "visitor_id": payload.get("visitorId") or payload.get("visitor_id"),
        "member_status": payload.get("memberStatus") or payload.get("member_status") or "trial",
        "locked": True,
        "created_at": now,
        "updated_at": now,
    }
    USER_ATTRIBUTION_TABLE.put_item(Item=safe_decimal(item))
    return response(200, {"linked": True, "attribution": item})


def membership_update(payload: Dict[str, Any], headers: Dict[str, Any]) -> Dict[str, Any]:
    expected_secret = os.environ.get("BILLING_WEBHOOK_SECRET", "").strip()
    if expected_secret and headers.get("x-inqsi-signature") != expected_secret:
        return response(401, {"error": "invalid_signature"})
    user_id = payload.get("userId") or payload.get("user_id")
    membership_id = payload.get("membershipId") or payload.get("membership_id")
    status = payload.get("status") or payload.get("member_status")
    if not user_id or not membership_id or status not in VALID_STATUSES:
        return response(400, {"error": "user_id_membership_id_and_valid_status_required"})
    attribution = USER_ATTRIBUTION_TABLE.get_item(Key={"user_id": user_id}).get("Item") or {}
    creator_id = attribution.get("creator_id") or payload.get("creatorId") or payload.get("creator_id") or "unattributed"
    now = utc_now()
    item = {
        "membership_id": membership_id,
        "user_id": user_id,
        "creator_id": creator_id,
        "referral_code": attribution.get("referral_code") or payload.get("referralCode") or payload.get("referral_code"),
        "billing_provider": payload.get("billingProvider") or payload.get("billing_provider") or "external_billing_provider",
        "billing_provider_customer_id": payload.get("billingProviderCustomerId") or payload.get("billing_provider_customer_id"),
        "plan": payload.get("plan") or "premium",
        "member_status": status,
        "amount_cents": payload.get("amountCents") or payload.get("amount_cents"),
        "period_start": payload.get("periodStart") or payload.get("period_start"),
        "period_end": payload.get("periodEnd") or payload.get("period_end"),
        "last_paid_at": payload.get("lastPaidAt") or payload.get("last_paid_at") or (now if status == LIVE_PAID else None),
        "updated_at": now,
        "created_at": payload.get("createdAt") or payload.get("created_at") or now,
    }
    MEMBERSHIP_TABLE.put_item(Item=safe_decimal(item))
    if attribution:
        attribution["member_status"] = status
        attribution["updated_at"] = now
        USER_ATTRIBUTION_TABLE.put_item(Item=safe_decimal(attribution))
    return response(200, {"stored": True, "membership": item})


def creator_metrics(creator_id: str) -> Dict[str, Any]:
    creator = CREATORS_TABLE.get_item(Key={"creator_id": creator_id}).get("Item")
    if not creator:
        return response(404, {"error": "creator_not_found"})
    active = MEMBERSHIP_TABLE.query(IndexName="CreatorStatusIndex", KeyConditionExpression=Key("creator_id").eq(creator_id) & Key("member_status").eq(LIVE_PAID)).get("Items", [])
    canceled = MEMBERSHIP_TABLE.query(IndexName="CreatorStatusIndex", KeyConditionExpression=Key("creator_id").eq(creator_id) & Key("member_status").eq("canceled")).get("Items", [])
    past_due = MEMBERSHIP_TABLE.query(IndexName="CreatorStatusIndex", KeyConditionExpression=Key("creator_id").eq(creator_id) & Key("member_status").eq("past_due")).get("Items", [])
    return response(200, {
        "creator": creator,
        "metrics": {
            "livePaidCustomers": len(active),
            "liveMrrCents": sum(int(item.get("amount_cents") or 0) for item in active),
            "canceledCustomers": len(canceled),
            "pastDueCustomers": len(past_due),
        },
        "privacy": "Creator reporting should default to aggregate counts."
    })


def handle_http(event: Dict[str, Any]) -> Dict[str, Any]:
    method = event.get("requestContext", {}).get("http", {}).get("method", event.get("httpMethod", "GET")).upper()
    path = event.get("rawPath") or event.get("path") or "/"
    headers = event.get("headers") or {}
    if method == "OPTIONS":
        return response(200, {"ok": True})
    if path == "/v1/creators" and method == "POST":
        return create_creator(body(event))
    if path == "/v1/creators" and method == "GET":
        items = CREATORS_TABLE.scan(Limit=100).get("Items", [])
        return response(200, {"creators": items, "count": len(items)})
    if path.startswith("/v1/creators/") and path.endswith("/metrics") and method == "GET":
        return creator_metrics(path.split("/")[3])
    if path == "/v1/attribution/capture" and method == "POST":
        return capture(body(event))
    if path == "/v1/attribution/link-user" and method == "POST":
        return link_user(body(event))
    if path == "/v1/memberships/webhook" and method == "POST":
        return membership_update(body(event), headers)
    return response(404, {"error": "creator_tracking_route_not_found", "path": path})


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    return handle_http(event or {})
