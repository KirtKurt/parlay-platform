import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import boto3
from boto3.dynamodb.conditions import Key

from sports.nba.algorithm import rank_nba_b11c1

dynamodb = boto3.resource("dynamodb")

SNAPSHOTS_TABLE = os.environ.get("SNAPSHOTS_TABLE", "")
SIGNALS_TABLE = os.environ.get("SIGNALS_TABLE", "")

snapshots_tbl = dynamodb.Table(SNAPSHOTS_TABLE) if SNAPSHOTS_TABLE else None
signals_tbl = dynamodb.Table(SIGNALS_TABLE) if SIGNALS_TABLE else None


def _resp(status: int, body: Any) -> Dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json",
            "access-control-allow-origin": "*",
            "access-control-allow-methods": "GET,POST,OPTIONS",
            "access-control-allow-headers": "content-type",
        },
        "body": json.dumps(body),
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_json(body: Optional[str]) -> Dict[str, Any]:
    if not body:
        return {}
    try:
        return json.loads(body)
    except Exception:
        return {}


# ---------------------------
# API HANDLER (API Gateway)
# ---------------------------
def lambda_handler(event, context):
    method = (event.get("httpMethod") or "").upper()
    path = event.get("path") or "/"

    if method == "OPTIONS":
        return _resp(200, {"ok": True})

    # Keep legacy hello route working
    if path == "/hello/" and method == "GET":
        return _resp(200, {"message": "hello world"})

    # Health
    if path in ("/health", "/v1/health") and method == "GET":
        return _resp(200, {"ok": True, "ts": _now_iso()})

    # Rank NBA combos: POST /v1/rank/nba
    if path == "/v1/rank/nba" and method == "POST":
        payload = _parse_json(event.get("body"))
        games = payload.get("games")
        if not isinstance(games, list) or len(games) != 3:
            return _resp(400, {"ok": False, "error": "Provide exactly 3 games in body.games"})
        return _resp(200, rank_nba_b11c1(games))

    # Write snapshot: POST /v1/snapshots
    if path == "/v1/snapshots" and method == "POST":
        if snapshots_tbl is None:
            return _resp(500, {"ok": False, "error": "SNAPSHOTS_TABLE not configured"})

        payload = _parse_json(event.get("body"))
        sport = payload.get("sport", "unknown")
        slate_id = payload.get("slate_id", "unspecified")
        asof = payload.get("asof", _now_iso())
        data = payload.get("data", {})

        pk = f"SPORT#{sport}"
        sk = f"ASOF#{asof}#SLATE#{slate_id}"

        snapshots_tbl.put_item(
            Item={
                "PK": pk,
                "SK": sk,
                "sport": sport,
                "slate_id": slate_id,
                "asof": asof,
                "data": data,
                "created_at": _now_iso(),
            }
        )
        return _resp(200, {"ok": True, "pk": pk, "sk": sk})

    # Read latest snapshots: GET /v1/snapshots?sport=nba&limit=5
    if path == "/v1/snapshots" and method == "GET":
        if snapshots_tbl is None:
            return _resp(500, {"ok": False, "error": "SNAPSHOTS_TABLE not configured"})

        qs = event.get("queryStringParameters") or {}
        sport = (qs.get("sport") or "unknown").lower()
        limit = int(qs.get("limit") or 5)

        pk = f"SPORT#{sport}"
        resp = snapshots_tbl.query(
            KeyConditionExpression=Key("PK").eq(pk),
            ScanIndexForward=False,
            Limit=limit,
        )
        return _resp(200, {"ok": True, "items": resp.get("Items", [])})

    return _resp(404, {"ok": False, "error": f"Route not found: {method} {path}"})


# ---------------------------
# SCHEDULER HANDLER (EventBridge)
# ---------------------------
def scheduler_handler(event, context):
    run_type = (event or {}).get("run", "unknown")
    ts = _now_iso()

    if snapshots_tbl is None:
        return {"ok": False, "error": "SNAPSHOTS_TABLE not configured"}

    snapshots_tbl.put_item(
        Item={
            "PK": "SYSTEM#SCHEDULER",
            "SK": f"RUN#{run_type}#TS#{ts}",
            "run": run_type,
            "ts": ts,
        }
    )

    return {"ok": True, "run": run_type, "ts": ts}
