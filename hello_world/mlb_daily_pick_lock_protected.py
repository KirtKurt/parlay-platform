from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

import mlb_daily_pick_lock
import mlb_daily_lock_coverage_patch
import mlb_daily_lock_ml_vector_preservation_patch
import mlb_daily_per_game_lock_patch

mlb_daily_lock_coverage_patch.apply(mlb_daily_pick_lock)
ML_VECTOR_PRESERVATION_STATUS = mlb_daily_lock_ml_vector_preservation_patch.apply(mlb_daily_pick_lock)
mlb_daily_per_game_lock_patch.apply(mlb_daily_pick_lock)
PER_GAME_LOCK_STATUS = {
    "ok": bool(getattr(mlb_daily_pick_lock, "_INQSI_MLB_DAILY_PER_GAME_LOCK_V1", False)),
    "version": getattr(mlb_daily_pick_lock, "MLB_DAILY_PER_GAME_LOCK_VERSION", None),
    "policy": getattr(mlb_daily_pick_lock, "LOCK_POLICY", None),
    "failClosed": True,
    "canonicalGameWriteAtOwnTMinus45": True,
}
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


def _attach_preservation_status(response: Any) -> Any:
    if not isinstance(response, dict):
        return response
    out = dict(response)
    body = out.get("body")
    try:
        payload = json.loads(body) if isinstance(body, str) else dict(body or {})
    except Exception:
        payload = {"rawBody": body}
    if isinstance(payload, dict):
        payload["mlLockVectorPreservation"] = ML_VECTOR_PRESERVATION_STATUS
        payload["perGameLockInstallation"] = PER_GAME_LOCK_STATUS
        out["body"] = json.dumps(payload)
    return out


def lambda_handler(event, context):
    event = event or {}
    if (event.get("httpMethod") or "").upper() == "OPTIONS":
        return _resp(200, {
            "ok": True,
            "mlLockVectorPreservation": ML_VECTOR_PRESERVATION_STATUS,
            "perGameLockInstallation": PER_GAME_LOCK_STATUS,
        })
    if ML_VECTOR_PRESERVATION_STATUS.get("ok") is not True:
        return _resp(
            500,
            {
                "ok": False,
                "sport": "mlb",
                "error": "MLB_DAILY_LOCK_ML_VECTOR_PRESERVATION_NOT_INSTALLED",
                "status": ML_VECTOR_PRESERVATION_STATUS,
            },
        )
    if PER_GAME_LOCK_STATUS.get("ok") is not True:
        return _resp(
            500,
            {
                "ok": False,
                "sport": "mlb",
                "error": "MLB_DAILY_PER_GAME_LOCK_NOT_INSTALLED",
                "status": PER_GAME_LOCK_STATUS,
            },
        )
    auth_error = _auth_error(event)
    if auth_error is not None:
        return auth_error
    return _attach_preservation_status(mlb_daily_pick_lock.lambda_handler(event, context))
