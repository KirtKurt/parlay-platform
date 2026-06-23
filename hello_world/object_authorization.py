import json
import os
from hmac import compare_digest
from typing import Any, Dict, Optional

try:
    import cyber_security
except Exception:
    cyber_security = None

TOKEN_ENV = "INQSI_ADMIN_API_TOKEN"


def response(status: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json",
            "access-control-allow-origin": "*",
            "access-control-allow-methods": "GET,POST,PATCH,OPTIONS",
            "access-control-allow-headers": "content-type,authorization,x-inqsi-admin-token,x-inqsi-member-id,x-inqsi-session-id,x-inqsi-device-id,x-inqsi-creator-handle",
        },
        "body": json.dumps(body),
    }


def headers(event: Dict[str, Any]) -> Dict[str, str]:
    return {str(k).lower(): str(v) for k, v in (event.get("headers") or {}).items() if v is not None}


def method(event: Dict[str, Any]) -> str:
    return (event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method") or "GET").upper()


def path(event: Dict[str, Any]) -> str:
    return (event.get("rawPath") or event.get("path") or "/").rstrip("/") or "/"


def query(event: Dict[str, Any]) -> Dict[str, str]:
    return event.get("queryStringParameters") or {}


def body(event: Dict[str, Any]) -> Dict[str, Any]:
    try:
        payload = json.loads(event.get("body") or "{}")
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def admin_token(event: Dict[str, Any]) -> str:
    h = headers(event)
    token = h.get("x-inqsi-admin-token") or h.get("x-admin-token") or ""
    auth = h.get("authorization") or ""
    if not token and auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    return str(token).strip()


def is_admin(event: Dict[str, Any]) -> bool:
    configured = str(os.environ.get(TOKEN_ENV) or "").strip()
    supplied = admin_token(event)
    return bool(configured and supplied and compare_digest(configured, supplied))


def member_header(event: Dict[str, Any]) -> str:
    h = headers(event)
    return str(h.get("x-inqsi-member-id") or h.get("x-member-id") or "").strip()


def creator_header(event: Dict[str, Any]) -> str:
    h = headers(event)
    return str(h.get("x-inqsi-creator-handle") or h.get("x-creator-handle") or "").strip().lower()


def log_denied(event: Dict[str, Any], reason: str, target_type: str, target_id: str) -> None:
    if cyber_security is None:
        return
    try:
        cyber_security.record_security_event("OBJECT_AUTH_DENIED", event, {
            "path": path(event),
            "method": method(event),
            "reason": reason,
            "targetType": target_type,
            "targetId": target_id,
        })
    except Exception:
        pass


def deny(event: Dict[str, Any], status: int, error: str, target_type: str, target_id: str) -> Dict[str, Any]:
    log_denied(event, error, target_type, target_id)
    return response(status, {"ok": False, "error": error, "targetType": target_type})


def require_member_owner(event: Dict[str, Any], target_member_id: str) -> Optional[Dict[str, Any]]:
    target = str(target_member_id or "").strip()
    if not target:
        return deny(event, 400, "target_member_id_required", "member", "")
    if is_admin(event):
        return None
    requester = member_header(event)
    if not requester:
        return deny(event, 401, "member_auth_required", "member", target)
    if requester != target:
        return deny(event, 403, "member_object_access_denied", "member", target)
    return None


def require_creator_owner(event: Dict[str, Any], target_handle: str) -> Optional[Dict[str, Any]]:
    target = str(target_handle or "").strip().lower()
    if not target:
        return deny(event, 400, "target_creator_handle_required", "creator", "")
    if is_admin(event):
        return None
    requester = creator_header(event)
    if not requester:
        return deny(event, 401, "creator_auth_required", "creator", target)
    if requester != target:
        return deny(event, 403, "creator_object_access_denied", "creator", target)
    return None


def target_member_for_route(event: Dict[str, Any]) -> Optional[str]:
    p = path(event)
    q = query(event)
    b = body(event)
    if p.startswith("/v1/inqsi/member-images/") or p.startswith("/v1/member-images/"):
        return p.rsplit("/", 1)[-1]
    if p in {"/v1/inqsi/member-images/upload", "/v1/member-images/upload"}:
        return str(b.get("member_id") or b.get("memberId") or b.get("user_id") or b.get("userId") or "").strip()
    if any(p.endswith(suffix) for suffix in ["/watchlist", "/watchlist/add", "/dashboard", "/bet-slip-check"]):
        user_id = str(q.get("user_id") or b.get("user_id") or b.get("memberId") or b.get("member_id") or "").strip()
        if user_id and user_id != "anonymous":
            return user_id
    return None


def target_creator_for_route(event: Dict[str, Any]) -> Optional[str]:
    p = path(event)
    q = query(event)
    if p in {"/v1/inqsi/creators/dashboard", "/v1/creators/dashboard"}:
        return str(q.get("handle") or "").strip()
    return None


def check(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    event = event or {}
    if method(event) == "OPTIONS":
        return None
    target_member = target_member_for_route(event)
    if target_member is not None:
        return require_member_owner(event, target_member)
    target_creator = target_creator_for_route(event)
    if target_creator is not None:
        return require_creator_owner(event, target_creator)
    return None
