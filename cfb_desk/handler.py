"""College desk Lambda. Own table. Does not call any other sport stack."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

import boto3

from engine import run_cfb_engine
from ingest import load_cfb_season

TABLE = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
PK = "CFB_DESK_STATUS"
SK = "CURRENT"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _read() -> dict[str, Any] | None:
    item = TABLE.get_item(Key={"PK": PK, "SK": SK}).get("Item")
    if not item:
        return None
    body = item.get("body")
    if isinstance(body, str):
        parsed = json.loads(body)
        return parsed if isinstance(parsed, dict) else None
    return None


def _starting() -> dict[str, Any]:
    return {
        "ok": True,
        "sport": "CFB",
        "mode": "STARTING",
        "source": "espn_public_scores",
        "market_model": False,
        "authority": None,
        "promotedResidual": False,
        "at": _now(),
        "board": [],
    }


def status_payload() -> dict[str, Any]:
    current = _read()
    return current if current else _starting()


def tick(event: Any | None = None) -> dict[str, Any]:
    supplied = event.get("games") if isinstance(event, dict) else None
    if isinstance(supplied, list) and supplied:
        season = {
            "games": supplied,
            "season": int(event.get("season") or supplied[0].get("season") or 2026),
            "week": int(event.get("week") or supplied[0].get("week") or 1),
            "errors": [],
        }
    else:
        season = load_cfb_season()
    if len(season["games"]) < 100:
        return {
            "ok": False,
            "sport": "CFB",
            "mode": "INGEST_FAILED",
            "at": _now(),
            "games": len(season["games"]),
            "errors": season.get("errors") or [],
        }
    report = run_cfb_engine(season["games"], {"season": season["season"], "week": season["week"]})
    leans = [row for row in report["board"] if row["side"] != "pass"]
    payload = {
        "ok": True,
        "sport": "CFB",
        "mode": "LIVE" if report["board"] else "NO_SLATE",
        "source": "espn_public_scores",
        "market_model": False,
        "at": _now(),
        "season": season["season"],
        "week": season["week"],
        "games": len(season["games"]),
        "authority": report["authority"],
        "promotedResidual": report["promotedResidual"],
        "failures": report["failures"],
        "excludedCupcakes": report["excludedCupcakes"],
        "metrics": report["metrics"],
        "boardCount": len(report["board"]),
        "leanCount": len(leans),
        "board": report["board"],
    }
    TABLE.put_item(Item={"PK": PK, "SK": SK, "updated_at": payload["at"], "body": json.dumps(payload, default=str)})
    summary = {key: value for key, value in payload.items() if key != "board"}
    summary["boardCount"] = payload["boardCount"]
    return summary


def _http(body: dict[str, Any], status: int = 200) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json", "cache-control": "no-store"},
        "body": json.dumps(body, default=str),
    }


def _is_http(event: Any) -> bool:
    return isinstance(event, dict) and bool(event.get("requestContext") or event.get("rawPath"))


def status_handler(event: Any, context: Any) -> dict[str, Any]:
    del context
    try:
        payload = status_payload()
    except Exception as exc:
        payload = {"ok": False, "sport": "CFB", "mode": "FAILED", "reason": str(exc)[:240]}
        if _is_http(event):
            return _http(payload, 503)
        return payload
    if _is_http(event):
        return _http(payload)
    return payload


def tick_handler(event: Any, context: Any) -> dict[str, Any]:
    del context
    try:
        result = tick(event if isinstance(event, dict) else None)
        return result
    except Exception as exc:
        return {"ok": False, "sport": "CFB", "mode": "FAILED", "reason": str(exc)[:240]}
