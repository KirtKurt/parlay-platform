"""Runtime patch for the MLB fifteen-minute pipeline."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

MODEL_VERSION = "MLB-LINE-MOVE-WINNER-V2-2026-07-02"
DEFAULT_START_AT_ET = "2026-07-03T01:00:00-04:00"


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return str(value)


def _resp(status: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {"statusCode": status, "headers": {"content-type": "application/json", "access-control-allow-origin": "*"}, "body": json.dumps(body, default=_json_default)}


def _body(response: Dict[str, Any]) -> Dict[str, Any]:
    raw = response.get("body") if isinstance(response, dict) else None
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _with_body(response: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(response or {})
    out.setdefault("statusCode", 200)
    out.setdefault("headers", {"content-type": "application/json", "access-control-allow-origin": "*"})
    out["body"] = json.dumps(body, default=_json_default)
    return out


def _parse_start(value: Optional[str]) -> Optional[datetime]:
    if not value or str(value).strip().lower() in {"off", "disabled", "false", "none"}:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("America/New_York"))
    return dt.astimezone(ZoneInfo("America/New_York"))


def _start_gate(event: Dict[str, Any]) -> Dict[str, Any]:
    if (event or {}).get("httpMethod"):
