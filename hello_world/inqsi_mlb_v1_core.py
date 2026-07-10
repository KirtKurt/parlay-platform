from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

MODEL_VERSION = "INQSI-MLB-v2.1-core-single-game-import-safe"
MODEL_CREATED_AT = "2026-07-09"

WEIGHTS = {
    "market_consensus": 0.34,
    "line_movement": 0.18,
    "book_agreement": 0.12,
    "real_book_ev": 0.18,
    "pull_depth": 0.08,
    "risk_guardrails": 0.10,
}
CONFIDENCE_TIERS = [(0.67, "Premium"), (0.60, "Solid"), (0.55, "Lean"), (0.50, "Coin Flip"), (0.00, "Pass")]


def _today_et() -> str:
    return datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    if isinstance(value, tuple):
        return list(value)
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


def _engine():
    # Keep imports lazy so /v1/mlb/model/version can never fail at Lambda cold-start
    # because an optional downstream prediction/audit dependency is unavailable.
    import mlb_game_winner_engine
    return mlb_game_winner_engine


def _engine_version() -> Dict[str, Any]:
    try:
        engine = _engine()
        return {
            "ok": True,
            "engine": getattr(engine, "ENGINE", "unknown"),
            "modelVersion": getattr(engine, "MODEL_VERSION", "unknown"),
            "promotionThreshold": getattr(engine, "PROMOTION_THRESHOLD", None),
            "fallbackPromotionThreshold": getattr(engine, "FALLBACK_THRESHOLD", None),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "engine": None, "modelVersion": None}


def model_version() -> Dict[str, Any]:
    engine_info = _engine_version()
    return {
        "ok": True,
        "sport": "mlb",
        "model_version": MODEL_VERSION,
        "game_winner_model": engine_info.get("modelVersion"),
        "game_winner_engine": engine_info.get("engine"),
        "engine_import_ok": engine_info.get("ok"),
        "engine_import_error": engine_info.get("error"),
        "created_at": MODEL_CREATED_AT,
        "pick_type": "individual_game_moneyline",
        "parlaysEnabled": False,
        "sourcePolicy": "The Odds API stored pull history only; no public sportsbook pages or manual odds for production picks.",
        "ranking": "EV + edge vs real book price + 15-minute line movement + book agreement + guardrails",
        "weights": WEIGHTS,
        "confidence_tiers": CONFIDENCE_TIERS,
        "data_architecture": {
            "lambda": True,
            "api_gateway": True,
            "dynamodb": True,
            "eventbridge_15_min": True,
            "daily_lock_t_minus_first_game": True,
        },
    }


def today(game_date: Optional[str] = None) -> Dict[str, Any]:
    game_date = game_date or _today_et()
    engine_info = _engine_version()
    return {
        "ok": True,
        "sport": "mlb",
        "date": game_date,
        "model_version": MODEL_VERSION,
        "game_winner_model": engine_info.get("modelVersion"),
        "engine_import_ok": engine_info.get("ok"),
        "priority": "individual_game_moneyline_picks",
        "parlaysEnabled": False,
        "message": "INQSI MLB production is individual game moneyline picks only. Parlays are disabled on the primary MLB surface.",
    }


def predictions(game_date: Optional[str] = None, limit: int = 500, store: bool = False) -> Dict[str, Any]:
    game_date = game_date or _today_et()
    try:
        engine = _engine()
        winners = engine.predict_all(game_date, store=store, limit=limit)
    except Exception as exc:
        return {
            "ok": False,
            "sport": "mlb",
            "date": game_date,
            "model_version": MODEL_VERSION,
            "error": str(exc),
            "winner_predictions": [],
            "count": 0,
        }
    return {
        "ok": True,
        "sport": "mlb",
        "date": game_date,
        "model_version": MODEL_VERSION,
        "game_winner_model": winners.get("modelVersion"),
        "priority": "individual_game_moneyline_picks",
        "parlaysEnabled": False,
        "count": winners.get("count", 0),
        "promotedCount": winners.get("promotedCount", 0),
        "allGamesPredicted": winners.get("allGamesPredicted"),
        "pullCount": winners.get("pullCount"),
        "latestPullAt": winners.get("latestPullAt"),
        "promotionThreshold": winners.get("promotionThreshold"),
        "fallbackPromotionThreshold": winners.get("fallbackPromotionThreshold"),
        "winner_predictions": winners.get("predictions") or [],
        "storage": {"requested": store, "gameWinnerStoredCount": winners.get("storedCount")},
        "parlay_analysis": {"enabled": False, "reason": "MLB production is individual game picks only."},
        "three_leg_parlay": {"ok": False, "disabled": True, "reason": "MLB production is individual game picks only."},
    }


def audit(game_date: Optional[str] = None) -> Dict[str, Any]:
    game_date = game_date or _today_et()
    return {
        "ok": True,
        "sport": "mlb",
        "date": game_date,
        "model_version": MODEL_VERSION,
        "message": "Use /v1/mlb/game-winners for current single-game predictions and settled-results endpoints for grading.",
        "parlaysEnabled": False,
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
        if path.endswith("/model/version"):
            return _resp(200, model_version())
        if path.endswith("/today"):
            return _resp(200, today(game_date))
        if path.endswith("/games") or path.endswith("/predictions") or path.endswith("/game-winners"):
            return _resp(200, predictions(game_date, limit, params.get("store", "false").lower() == "true"))
        if path.endswith("/audit"):
            return _resp(200, audit(game_date))
        return _resp(404, {"ok": False, "error": f"Route not found: {method} {path}"})
    except Exception as exc:
        return _resp(500, {"ok": False, "sport": "mlb", "model_version": MODEL_VERSION, "error": str(exc)})


def lambda_handler(event, context):
    return handle(event, context)
