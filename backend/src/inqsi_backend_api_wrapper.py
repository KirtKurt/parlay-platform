from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

import boto3
from boto3.dynamodb.conditions import Key

import inqsi_backend_api

CORS_HEADERS = getattr(inqsi_backend_api, "CORS_HEADERS", {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "content-type,authorization,x-inqsi-member-id,x-inqsi-admin-token",
    "Access-Control-Allow-Methods": "GET,POST,PATCH,DELETE,OPTIONS",
})
DDB = boto3.resource("dynamodb")
SNAPSHOTS_TABLE = os.environ.get("SNAPSHOTS_TABLE", "")
PULLS = DDB.Table(SNAPSHOTS_TABLE) if SNAPSHOTS_TABLE else None


def _safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: _safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_safe(v) for v in value]
    return value


def _resp(status: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {"statusCode": status, "headers": CORS_HEADERS, "body": json.dumps(_safe(body))}


def _path(event: Dict[str, Any]) -> str:
    return ((event or {}).get("rawPath") or (event or {}).get("path") or "/").rstrip("/") or "/"


def _query(event: Dict[str, Any]) -> Dict[str, str]:
    return (event or {}).get("queryStringParameters") or {}


def _today_et() -> str:
    return datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York")).date().isoformat()


def _stored_game_winners(slate_date: str) -> Dict[str, Any]:
    if PULLS is None:
        return {"ok": False, "error": "SNAPSHOTS_TABLE not configured", "predictions": [], "count": 0}
    try:
        resp = PULLS.query(KeyConditionExpression=Key("PK").eq(f"GAME_WINNERS#mlb#{slate_date}"), ScanIndexForward=True, Limit=500)
        rows = []
        for item in resp.get("Items", []):
            data = item.get("data") if isinstance(item.get("data"), dict) else item
            if isinstance(data, dict):
                rows.append(data)
        rows.sort(key=lambda r: (r.get("rank") is None, r.get("rank") or 999, str(r.get("commenceTime") or "")))
        return {"ok": True, "predictions": rows, "count": len(rows)}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "predictions": [], "count": 0}


def _mlb_route(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    path = _path(event)
    if not path.startswith("/v1/mlb"):
        return None
    params = _query(event)
    slate_date = params.get("game_date_et") or params.get("date") or _today_et()

    if path == "/v1/mlb/model/version":
        return _resp(200, {
            "ok": True,
            "sport": "mlb",
            "model_version": "INQSI-MLB-v2.1-core-backend-smoke-safe",
            "game_winner_model": "INQSI-MLB-SINGLE-GAME-ML-v2.1-aws-sam-production",
            "pick_type": "individual_game_moneyline",
            "parlaysEnabled": False,
            "sourcePolicy": "The Odds API stored pull history; AWS EventBridge/Lambda/DynamoDB are the production odds path.",
            "smokeRoute": "inqsi_backend_api_wrapper",
            "checkedAt": datetime.now(timezone.utc).isoformat(),
        })

    if path in {"/v1/mlb/today", "/v1/mlb/game-winners", "/v1/mlb/predictions", "/v1/mlb/games"}:
        stored = _stored_game_winners(slate_date)
        predictions = stored.get("predictions") or []
        body = {
            "ok": stored.get("ok", False),
            "sport": "mlb",
            "date": slate_date,
            "model_version": "INQSI-MLB-v2.1-core-backend-smoke-safe",
            "game_winner_model": "INQSI-MLB-SINGLE-GAME-ML-v2.1-aws-sam-production",
            "pick_type": "individual_game_moneyline",
            "parlaysEnabled": False,
            "count": len(predictions),
            "promotedCount": len([r for r in predictions if r.get("promoted") or str(r.get("promotionStatus") or "").startswith("PROMOTED")]),
            "winner_predictions": predictions,
            "predictions": predictions,
            "message": "Smoke-safe MLB route. Official picks require successful /v1/pull/mlb live ingest and stored GAME_WINNERS rows.",
        }
        if stored.get("error"):
            body["error"] = stored.get("error")
        return _resp(200, body)

    return None


def lambda_handler(event, context):
    routed = _mlb_route(event or {})
    if routed is not None:
        return routed
    return inqsi_backend_api.lambda_handler(event, context)
