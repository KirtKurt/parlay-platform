from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

import mlb_game_winner_engine

MODEL_VERSION = "INQSI-MLB-v2.0-single-game-core"
MODEL_CREATED_AT = "2026-07-09"


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


def today(game_date: Optional[str] = None) -> Dict[str, Any]:
    date = game_date or _today_et()
    winners = mlb_game_winner_engine.predict_all(date, store=False, limit=500)
    return {
        "ok": True,
        "sport": "mlb",
        "date": date,
        "model_version": MODEL_VERSION,
        "game_winner_model": winners.get("modelVersion"),
        "platformVersion": "INQSI_MLB_SINGLE_GAME_V2",
        "priority": "individual_game_moneyline_picks",
        "parlaysEnabled": False,
        "sourcePolicy": "The Odds API stored pull history only; no public sportsbook pages or manual odds for production picks.",
        "pullCount": winners.get("pullCount"),
        "latestPullAt": winners.get("latestPullAt"),
        "latestPullAgeMinutes": winners.get("latestPullAgeMinutes"),
        "gameCount": winners.get("gameCount", 0),
        "predictionCount": winners.get("count", 0),
        "promotionCount": winners.get("promotionCount", 0),
        "message": "MLB production is individual game moneyline picks only. Parlays are disabled on the primary MLB surface.",
    }


def games(game_date: Optional[str] = None, limit: int = 500) -> Dict[str, Any]:
    date = game_date or _today_et()
    winners = mlb_game_winner_engine.predict_all(date, store=False, limit=limit)
    return {
        "ok": True,
        "sport": "mlb",
        "date": date,
        "model_version": MODEL_VERSION,
        "game_winner_model": winners.get("modelVersion"),
        "priority": "individual_game_moneyline_picks",
        "parlaysEnabled": False,
        "count": winners.get("count", 0),
        "allGamesPredicted": winners.get("allGamesPredicted"),
        "games": winners.get("predictions") or [],
        "pullCount": winners.get("pullCount"),
        "latestPullAt": winners.get("latestPullAt"),
        "promotionCount": winners.get("promotionCount"),
    }


def predictions(game_date: Optional[str] = None, limit: int = 500, store: bool = False) -> Dict[str, Any]:
    date = game_date or _today_et()
    winners = mlb_game_winner_engine.predict_all(date, store=store, limit=limit)
    return {
        "ok": True,
        "sport": "mlb",
        "date": date,
        "model_version": MODEL_VERSION,
        "game_winner_model": winners.get("modelVersion"),
        "priority": "individual_game_moneyline_picks",
        "parlaysEnabled": False,
        "count": winners.get("count", 0),
        "promotionCount": winners.get("promotionCount", 0),
        "underdogCount": winners.get("underdogCount", 0),
        "favoriteCount": winners.get("favoriteCount", 0),
        "winner_predictions": winners.get("predictions") or [],
        "market_research_count": 0,
        "market_research_rows": [],
        "parlay_analysis": {"enabled": False, "reason": "MLB production is individual game picks only."},
        "three_leg_parlay": {"ok": False, "disabled": True, "reason": "MLB production is individual game picks only."},
        "storage": {"requested": store, "gameWinnerStoredCount": winners.get("storedCount")},
    }


def audit(game_date: Optional[str] = None) -> Dict[str, Any]:
    date = game_date or _today_et()
    return {
        "ok": True,
        "sport": "mlb",
        "date": date,
        "model_version": MODEL_VERSION,
        "predictions": mlb_game_winner_engine.predict_all(date, store=False, limit=500),
        "parlaysEnabled": False,
        "message": "Use settled results endpoints for grading; this audit confirms the current single-game prediction surface.",
    }


def model_version() -> Dict[str, Any]:
    return {
        "ok": True,
        "sport": "mlb",
        "model_version": MODEL_VERSION,
        "game_winner_model": mlb_game_winner_engine.MODEL_VERSION,
        "created_at": MODEL_CREATED_AT,
        "pick_type": "individual_game_moneyline",
        "parlaysEnabled": False,
        "ranking": "EV + edge vs real book price + 15-minute line movement + guardrails",
        "data_source": "The Odds API stored pull history",
    }


def handle(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    event = event or {}
    method = (event.get("httpMethod") or "").upper()
    path = event.get("path") or event.get("rawPath") or ""
    params = _params(event)
    if method == "OPTIONS":
        return _resp(200, {"ok": True})
    try:
        game_date = params.get("game_date_et") or params.get("date") or _today_et()
        limit = min(max(int(params.get("limit") or 500), 1), 500)
        if path.endswith("/today"):
            return _resp(200, today(game_date))
        if path.endswith("/games"):
            return _resp(200, games(game_date, limit))
        if path.endswith("/predictions") or path.endswith("/game-winners"):
            return _resp(200, predictions(game_date, limit, params.get("store", "false").lower() == "true"))
        if path.endswith("/audit"):
            return _resp(200, audit(game_date))
        if path.endswith("/model/version"):
            return _resp(200, model_version())
        return _resp(404, {"ok": False, "error": f"Route not found: {method} {path}"})
    except Exception as exc:
        return _resp(500, {"ok": False, "sport": "mlb", "error": str(exc)})


def lambda_handler(event, context):
    return handle(event, context)
