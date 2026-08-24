"""AWS Lambda handlers."""
from __future__ import annotations

import json
from typing import Any, Mapping

from .controller import run_action


def _event_action(event: Any, default: str) -> str:
    if isinstance(event, Mapping):
        if event.get("action"):
            return str(event["action"])
        query = event.get("queryStringParameters")
        if isinstance(query, Mapping) and query.get("action"):
            return str(query["action"])
    return default


def _safe_error(exc: Exception) -> dict[str, Any]:
    response = getattr(exc, "response", {})
    code = None
    if isinstance(response, Mapping):
        error = response.get("Error")
        if isinstance(error, Mapping):
            code = error.get("Code")
    return {
        "ok": False,
        "status": "FAILED",
        "error_code": str(code or type(exc).__name__)[:100],
        "reason": str(exc)[:240],
    }


def autonomous_handler(event: Any, context: Any) -> dict[str, Any]:
    del context
    try:
        return run_action(_event_action(event, "autonomous_tick"))
    except Exception as exc:
        return _safe_error(exc)


def live_handler(event: Any, context: Any) -> dict[str, Any]:
    del context
    try:
        return run_action(_event_action(event, "live_tick"))
    except Exception as exc:
        return _safe_error(exc)


def training_handler(event: Any, context: Any) -> dict[str, Any]:
    del context
    try:
        return run_action(_event_action(event, "train"))
    except Exception as exc:
        return _safe_error(exc)


def status_handler(event: Any, context: Any) -> dict[str, Any]:
    del context
    try:
        result = run_action(_event_action(event, "status"))
        return {
            "statusCode": 200,
            "headers": {"content-type": "application/json", "cache-control": "no-store"},
            "body": json.dumps(result, sort_keys=True, default=str),
        }
    except Exception as exc:
        return {
            "statusCode": 503,
            "headers": {"content-type": "application/json", "cache-control": "no-store"},
            "body": json.dumps(_safe_error(exc), sort_keys=True),
        }
