import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict

import boto3
from boto3.dynamodb.conditions import Key

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "content-type,authorization,x-inqsi-session",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
}

DDB = boto3.resource("dynamodb")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    if isinstance(value, list):
        return [safe(item) for item in value]
    if isinstance(value, dict):
        return {key: safe(item) for key, item in value.items()}
    return value


def response(status_code: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {"statusCode": status_code, "headers": CORS_HEADERS, "body": json.dumps(safe(body))}


def body(event: Dict[str, Any]) -> Dict[str, Any]:
    raw = event.get("body")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def table(env_name: str):
    name = os.environ.get(env_name)
    return DDB.Table(name) if name else None


def create_session(payload: Dict[str, Any]) -> Dict[str, Any]:
    members = table("MEMBERS_TABLE")
    sessions = table("MEMBER_SESSIONS_TABLE")
    if not members or not sessions:
        return response(503, {"status": "working_on_it", "message": "Member tables are not configured."})
    email = str(payload.get("email") or "").strip().lower()
    external_subject = str(payload.get("externalSubject") or payload.get("external_subject") or "").strip()
    provider = str(payload.get("provider") or "external").strip().lower()
    if not email or not external_subject:
        return response(400, {"error": "email_and_external_subject_required"})
    now = utc_now()
    member_id = f"member_{uuid.uuid5(uuid.NAMESPACE_DNS, provider + ':' + external_subject).hex[:16]}"
    member = {
        "member_id": member_id,
        "email": email,
        "provider": provider,
        "external_subject": external_subject,
        "member_status": payload.get("memberStatus") or payload.get("member_status") or "trial",
        "created_at": payload.get("createdAt") or payload.get("created_at") or now,
        "updated_at": now,
    }
    session_id = f"sess_{uuid.uuid4().hex}"
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat().replace("+00:00", "Z")
    session = {
        "session_id": session_id,
        "member_id": member_id,
        "created_at": now,
        "expires_at": expires_at,
        "active": True,
    }
    members.put_item(Item=member)
    sessions.put_item(Item=session)
    return response(200, {"created": True, "member": {"member_id": member_id, "member_status": member["member_status"]}, "session": {"session_id": session_id, "expires_at": expires_at}})


def get_session(event: Dict[str, Any]) -> Dict[str, Any]:
    sessions = table("MEMBER_SESSIONS_TABLE")
    if not sessions:
        return response(503, {"status": "working_on_it", "message": "Member session table is not configured."})
    session_id = (event.get("headers") or {}).get("x-inqsi-session")
    if not session_id:
        return response(401, {"error": "session_required"})
    item = sessions.get_item(Key={"session_id": session_id}).get("Item")
    if not item or not item.get("active"):
        return response(401, {"error": "session_not_active"})
    if item.get("expires_at", "") <= utc_now():
        return response(401, {"error": "session_expired"})
    return response(200, {"active": True, "session": item})


def end_session(event: Dict[str, Any]) -> Dict[str, Any]:
    sessions = table("MEMBER_SESSIONS_TABLE")
    if not sessions:
        return response(503, {"status": "working_on_it", "message": "Member session table is not configured."})
    session_id = (event.get("headers") or {}).get("x-inqsi-session")
    if not session_id:
        return response(400, {"error": "session_required"})
    sessions.update_item(Key={"session_id": session_id}, UpdateExpression="SET active = :a", ExpressionAttributeValues={":a": False})
    return response(200, {"ended": True})


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    method = event.get("requestContext", {}).get("http", {}).get("method", event.get("httpMethod", "GET")).upper()
    path = event.get("rawPath") or event.get("path") or "/"
    if method == "OPTIONS":
        return response(200, {"ok": True})
    if path == "/v1/member/session" and method == "POST":
        return create_session(body(event))
    if path == "/v1/member/session" and method == "GET":
        return get_session(event)
    if path == "/v1/member/session/end" and method == "POST":
        return end_session(event)
    return response(404, {"error": "member_route_not_found", "path": path})
