from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

import mlb_daily_pick_lock

ADMIN_TOKEN = os.environ.get("INQSI_ADMIN_API_TOKEN", "")


def _resp(status: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json",
            "access-control-allow-origin": "*",
            "access-control-allow-headers": "content-type,authorization,x-inqsi-admin-token",
            "access-control-allow-methods": "GET,POST,OPTIONS",
        },
        "body": json.dumps(body),
    }


def _header(event: Dict[str, Any], name: str) -> str:
    headers = event.get("headers") or {}
    if isinstance(headers, dict):
        for key, value in headers.items():
            if str(key).lower() == name.lower():
                return str(value or "")
    return ""


def _auth_error(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    method = (event.get("httpMethod") or "").upper()
    # EventBridge scheduled invocations have no HTTP method and remain allowed.
    # GET status/today endpoints are read-only and remain public.
    if not (event.get("httpMethod") or event.get("requestContext")):
        return None
    if method in {"", "GET", "OPTIONS"}:
        return None
    if not ADMIN_TOKEN:
        return _resp(500, {"ok": False, "sport": "mlb", "error": "INQSI_ADMIN_API_TOKEN_NOT_CONFIGURED"})
    token = _header(event, "x-inqsi-admin-token").strip()
    auth = _header(event, "authorization").strip()
    if auth.lower().startswith("bearer "):
        auth = auth.split(" ", 1)[1].strip()
    if token == ADMIN_TOKEN or auth == ADMIN_TOKEN:
        return None
    return _resp(401, {"ok": False, "sport": "mlb", "error": "ADMIN_TOKEN_REQUIRED"})


def lambda_handler(event, context):
    event = event or {}
    if (event.get("httpMethod") or "").upper() == "OPTIONS":
        return _resp(200, {"ok": True})
    auth_error = _auth_error(event)
    if auth_error is not None:
        return auth_error
    return mlb_daily_pick_lock.lambda_handler(event, context)
