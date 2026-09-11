from __future__ import annotations

import json
from typing import Any, Dict

from sportsbook_catalog import audit_sportsbooks

VERSION = "INQSI-ARB-v3"


def _response(status: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json",
            "access-control-allow-origin": "*",
            "access-control-allow-methods": "GET,OPTIONS",
            "cache-control": "no-store",
        },
        "body": json.dumps(body, default=str),
    }


def lambda_handler(event, context):
    event = event or {}
    method = str(event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method") or "GET").upper()
    if method == "OPTIONS":
        return _response(200, {"ok": True})
    if method != "GET":
        return _response(405, {"ok": False, "error": "METHOD_NOT_ALLOWED"})

    query = {str(k): str(v) for k, v in (event.get("queryStringParameters") or {}).items() if v is not None}
    regions = query.get("regions") or None
    markets = query.get("markets") or None
    all_sports = query.get("all", "false").lower() == "true"

    try:
        books, meta = audit_sportsbooks(regions=regions, markets=markets, all_sports=all_sports)
    except Exception as exc:
        return _response(503, {
            "ok": False,
            "complete": False,
            "version": VERSION,
            "error": "SPORTSBOOK_AUDIT_FAILED",
            "detail": type(exc).__name__,
        })

    body = {
        "ok": bool(meta.get("ok")),
        "complete": bool(meta.get("complete")),
        "version": VERSION,
        "sportsbook_count": len(books),
        "sportsbooks": books,
        **meta,
    }
    return _response(200 if body["complete"] else 503, body)
