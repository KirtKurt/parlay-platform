import json
import os
from hmac import compare_digest
from typing import Any, Dict, Optional


TOKEN_ENV = "INQSI_ADMIN_API_TOKEN"


def response(status: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json",
            "access-control-allow-origin": "*",
            "access-control-allow-methods": "GET,POST,PATCH,OPTIONS",
            "access-control-allow-headers": "content-type,authorization,x-inqsi-admin-token,x-inqsi-member-id,x-inqsi-session-id",
        },
        "body": json.dumps(body),
    }


def headers(event: Dict[str, Any]) -> Dict[str, str]:
    return {str(k).lower(): str(v) for k, v in (event.get("headers") or {}).items() if v is not None}


def method(event: Dict[str, Any]) -> str:
    return (event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method") or "GET").upper()


def path(event: Dict[str, Any]) -> str:
    return (event.get("rawPath") or event.get("path") or "/").rstrip("/") or "/"


def is_admin_path(p: str, m: str) -> bool:
    if p.startswith("/v1/inqsi/admin/") or p.startswith("/v1/admin/"):
        return True
    if p in {"/v1/inqsi/creators", "/v1/creators"} and m == "GET":
        return True
    if p.startswith("/v1/inqsi/moderation/") or p.startswith("/v1/moderation/"):
        return True
    return False


def supplied_token(event: Dict[str, Any]) -> str:
    h = headers(event)
    token = h.get("x-inqsi-admin-token") or h.get("x-admin-token") or ""
    auth = h.get("authorization") or ""
    if not token and auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    return str(token).strip()


def check(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    event = event or {}
    p = path(event)
    m = method(event)
    if m == "OPTIONS":
        return None
    if not is_admin_path(p, m):
        return None

    configured = str(os.environ.get(TOKEN_ENV) or "").strip()
    if not configured:
        return response(503, {
            "ok": False,
            "error": "admin_auth_not_configured",
            "message": "Admin routes are locked until INQSI_ADMIN_API_TOKEN is configured.",
        })

    provided = supplied_token(event)
    if not provided:
        return response(401, {"ok": False, "error": "admin_token_required"})
    if not compare_digest(provided, configured):
        return response(403, {"ok": False, "error": "admin_token_invalid"})
    return None
