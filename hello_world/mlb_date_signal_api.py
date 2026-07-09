from __future__ import annotations

import json
import os
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

import mlb_game_winner_engine

SNAPSHOTS_TABLE = os.environ.get("SNAPSHOTS_TABLE", "")
TARGET_SUCCESS_RATE = Decimal("75")
MLB_PULL_MODE = "ROLLING_15_MIN_ONLY"
MLB_PULL_T = "HOT"


def _today_et() -> str:
    return datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    return str(value)


def _resp(status: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json",
            "access-control-allow-origin": "*",
            "access-control-allow-headers": "content-type,authorization,x-inqsi-admin-token",
            "access-control-allow-methods": "GET,POST,OPTIONS",
        },
        "body": json.dumps(body, default=_json_default),
    }


def _params(event: Dict[str, Any]) -> Dict[str, str]:
    return event.get("queryStringParameters") or {}


def _game_date_from_params(params: Dict[str, str]) -> str:
    return params.get("game_date_et") or params.get("slate_date_et") or params.get("date") or _today_et()


def source_status() -> Dict[str, Any]:
    return {
        "ok": True,
        "sport": "mlb",
        "source": "the_odds_api_stored_pull_history",
        "platformVersion": "INQSI_MLB_SINGLE_GAME_PLATFORM_V2",
        "modelVersion": mlb_game_winner_engine.MODEL_VERSION,
        "snapshotsTableConfigured": bool(SNAPSHOTS_TABLE),
        "parlaysEnabled": False,
        "message": "MLB V2 primary surface is individual game moneyline picks only. Legacy parlay signal output is disabled.",
    }


def movement_deltas(game_date: str, limit: int = 500) -> Dict[str, Any]:
    data = mlb_game_winner_engine.predict_all(game_date, store=False, limit=limit)
    return {
        "ok": True,
        "sport": "mlb",
        "game_date_et": game_date,
        "date_isolated": True,
        "pull_mode": MLB_PULL_MODE,
        "snapshot_t_filter": MLB_PULL_T,
        "count": data.get("count", 0),
        "deltas": data.get("predictions") or [],
        "message": "MLB V2 uses the single-game EV/promotion engine; legacy parlay deltas are disabled.",
    }


def results_status() -> Dict[str, Any]:
    return source_status()


def _legacy_payload(game_date: Optional[str], limit: int, store: bool, include_no_edge: bool) -> Dict[str, Any]:
    game_date = game_date or _today_et()
    data = mlb_game_winner_engine.predict_all(game_date, store=store, limit=limit)
    rows = data.get("predictions") or []
    if not include_no_edge:
        rows = [row for row in rows if row.get("promoted")]
    return {
        "ok": True,
        "sport": "mlb",
        "game_date_et": game_date,
        "date_isolated": True,
        "pull_mode": MLB_PULL_MODE,
        "snapshot_t_filter": MLB_PULL_T,
        "snapshot_partition": f"SPORT#mlb#DATE#{game_date}",
        "stored": store,
        "stored_count": data.get("storedCount") or 0,
        "storage_status": "CONNECTED" if store else "NOT_REQUESTED",
        "count": len(rows),
        "movement_count": data.get("count", 0),
        "individual_prediction_count": len(rows),
        "actionable_count": len([row for row in rows if row.get("promoted")]),
        "status_counts": {"PROMOTED": len([row for row in rows if row.get("promoted")]), "WATCHLIST_OR_NO_PLAY": len([row for row in rows if not row.get("promoted")])},
        "advanced_context_status": {"connected": False, "reason": "MLB V2 production currently uses odds history, book price, EV, and line movement. Advanced stat feeds are not allowed to fake confidence."},
        "advanced_context_counts": {"eligible_count": 0, "blocked_count": len(rows), "blockers": {"advanced_mlb_stat_feeds_not_connected": len(rows)}},
        "target_success_rate": 75,
        "display_confidence_scores": True,
        "message": "MLB V2 returns individual game moneyline picks only. Three-leg parlay output is disabled so production cannot surface MLB parlays by accident.",
        "previous_asof": None,
        "latest_asof": data.get("latestPullAt"),
        "game_predictions": rows,
        "hot_sides": rows,
        "three_leg_parlay": {"ok": False, "disabled": True, "reason": "MLB production is individual game picks only."},
    }


def hot_sides(game_date: str, limit: int = 500, store: bool = False, include_no_edge: bool = True) -> Dict[str, Any]:
    return _legacy_payload(game_date, limit, store, include_no_edge)


def handle(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    event = event or {}
    method = (event.get("httpMethod") or "").upper()
    if method == "OPTIONS":
        return _resp(200, {"ok": True})
    params = _params(event)
    path = event.get("path") or event.get("rawPath") or ""
    if path.endswith("/status") or path.endswith("/sources/mlb/status"):
        return _resp(200, source_status())
    if path.endswith("/deltas"):
        return _resp(200, movement_deltas(_game_date_from_params(params), min(max(int(params.get("limit") or 500), 1), 500)))
    return _resp(200, hot_sides(game_date=_game_date_from_params(params), limit=min(max(int(params.get("limit") or 500), 1), 500), store=str(params.get("store", "false")).lower() == "true", include_no_edge=str(params.get("include_no_edge", "true")).lower() != "false"))


def lambda_handler(event, context):
    return handle(event, context)
