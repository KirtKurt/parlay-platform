from __future__ import annotations

import json
import math
import os
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import boto3
from boto3.dynamodb.conditions import Key

from mlb_date_signal_api import hot_sides, source_status
from mlb_audit import evaluate_mlb_predictions, settlement_proof_report
import mlb_game_winner_engine

MODEL_VERSION = "INQSI-MLB-v1.1-core-single-game"
MODEL_CREATED_AT = "2026-07-09"
MINIMUM_PULLS = int(os.environ.get("MLB_MIN_PULLS_FOR_LOCK", "4"))

WEIGHTS = {
    "market_consensus": 0.34,
    "line_movement": 0.18,
    "book_agreement": 0.12,
    "real_book_ev": 0.18,
    "pull_depth": 0.08,
    "risk_guardrails": 0.10,
}
CONFIDENCE_TIERS = [(0.67, "Premium"), (0.60, "Solid"), (0.55, "Lean"), (0.50, "Coin Flip"), (0.00, "Pass")]

PREDICTIONS_TABLE = os.environ.get("PREDICTIONS_TABLE", "")
RAW_ARCHIVE_BUCKET = os.environ.get("RAW_ARCHIVE_BUCKET", "")

dynamodb = boto3.resource("dynamodb")
predictions_tbl = dynamodb.Table(PREDICTIONS_TABLE) if PREDICTIONS_TABLE else None


def _today_et() -> str:
    return datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


def _resp(status: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json",
            "access-control-allow-origin": "*",
            "access-control-allow-headers": "content-type",
            "access-control-allow-methods": "GET,POST,OPTIONS",
        },
        "body": json.dumps(body, default=_json_default),
    }


def _params(event: Dict[str, Any]) -> Dict[str, str]:
    return event.get("queryStringParameters") or {}


def probability_from_score(score_diff: float, k: float = 0.075) -> float:
    try:
        return 1.0 / (1.0 + math.exp(-k * float(score_diff)))
    except Exception:
        return 0.5


def confidence_tier(prob: float) -> str:
    edge = abs(float(prob) - 0.5) + 0.5
    for threshold, label in CONFIDENCE_TIERS:
        if edge >= threshold:
            return label
    return "Pass"


def today(game_date: Optional[str] = None) -> Dict[str, Any]:
    game_date = game_date or _today_et()
    return {
        "ok": True,
        "sport": "mlb",
        "date": game_date,
        "model_version": MODEL_VERSION,
        "game_winner_model": mlb_game_winner_engine.MODEL_VERSION,
        "source_status": source_status(),
        "priority": "individual_game_moneyline_picks",
        "parlays": "disabled_in_primary_mlb_surface",
        "message": "INQSI MLB v1.1 is the production single-game moneyline surface. It uses stored Odds API pull history, real book prices, EV, and promotion guardrails. Parlays are not part of the primary MLB product.",
    }


def games(game_date: Optional[str] = None, limit: int = 80) -> Dict[str, Any]:
    game_date = game_date or _today_et()
    winners = mlb_game_winner_engine.predict_all(game_date, store=False, limit=limit)
    return {
        "ok": True,
        "sport": "mlb",
        "date": game_date,
        "model_version": MODEL_VERSION,
        "game_winner_model": winners.get("modelVersion"),
        "priority": "individual_game_moneyline_picks",
        "count": winners.get("count", 0),
        "promotedCount": winners.get("promotedCount", 0),
        "allGamesPredicted": winners.get("allGamesPredicted"),
        "games": winners.get("predictions") or [],
        "pullCount": winners.get("pullCount"),
        "latestPullAt": winners.get("latestPullAt"),
        "primaryBook": winners.get("primaryBook"),
        "promotionPolicy": winners.get("promotionPolicy"),
    }


def predictions(game_date: Optional[str] = None, limit: int = 80, store: bool = False) -> Dict[str, Any]:
    game_date = game_date or _today_et()
    winners = mlb_game_winner_engine.predict_all(game_date, store=store, limit=500)
    market_research = hot_sides(game_date=game_date, limit=limit, store=False, include_no_edge=True)
    return {
        "ok": True,
        "sport": "mlb",
        "date": game_date,
        "model_version": MODEL_VERSION,
        "game_winner_model": winners.get("modelVersion"),
        "priority": "individual_game_moneyline_picks",
        "parlay_status": "disabled_in_primary_mlb_surface",
        "count": winners.get("count", 0),
        "promotedCount": winners.get("promotedCount", 0),
        "allGamesPredicted": winners.get("allGamesPredicted"),
        "winner_predictions": winners.get("predictions") or [],
        "market_research_count": market_research.get("count", 0),
        "market_research_status_counts": market_research.get("status_counts") or {},
        "advanced_context_status": market_research.get("advanced_context_status"),
        "storage": {
            "requested": store,
            "gameWinnerStoredCount": winners.get("storedCount"),
            "marketRowsStoredCount": 0,
        },
        "latestPullAt": winners.get("latestPullAt"),
        "primaryBook": winners.get("primaryBook"),
        "promotionPolicy": winners.get("promotionPolicy"),
    }


def _prediction_items(game_date: str) -> List[Dict[str, Any]]:
    if predictions_tbl is None:
        return []
    resp = predictions_tbl.query(KeyConditionExpression=Key("PK").eq(f"PRED#mlb#{game_date}"))
    return resp.get("Items", [])


def audit(game_date: Optional[str] = None) -> Dict[str, Any]:
    game_date = game_date or _today_et()
    evaluation = evaluate_mlb_predictions(game_date)
    items = _prediction_items(game_date)
    settled = [item for item in items if item.get("status") in {"CORRECT", "WRONG"}]
    brier_rows = []
    for item in settled:
        score_payload = item.get("inqsi_v1_score") or {}
        prob = float(score_payload.get("win_probability") or 0.5)
        actual = 1.0 if item.get("status") == "CORRECT" else 0.0
        brier_rows.append((prob - actual) ** 2)
    brier = round(sum(brier_rows) / len(brier_rows), 4) if brier_rows else None
    return {
        "ok": True,
        "sport": "mlb",
        "date": game_date,
        "model_version": MODEL_VERSION,
        "evaluation": evaluation,
        "brier_score": brier,
        "calibration": calibration(settled),
        "ranked_pick_accuracy": ranked_accuracy(settled),
        "suggested_weight_adjustments": suggested_weight_adjustments(settled),
        "settlement_proof": settlement_proof_report(slate_date=game_date, fetch_scores=False),
    }


def calibration(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    buckets = {"50_55": [], "55_60": [], "60_67": [], "67_plus": []}
    for row in rows:
        score_payload = row.get("inqsi_v1_score") or {}
        prob = float(score_payload.get("win_probability") or 0.5)
        actual = 1 if row.get("status") == "CORRECT" else 0
        if prob < 0.55:
            buckets["50_55"].append(actual)
        elif prob < 0.60:
            buckets["55_60"].append(actual)
        elif prob < 0.67:
            buckets["60_67"].append(actual)
        else:
            buckets["67_plus"].append(actual)
    return {k: {"count": len(v), "hit_rate": round(sum(v) / len(v), 4) if v else None} for k, v in buckets.items()}


def ranked_accuracy(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    sortable = [(float((row.get("inqsi_v1_score") or {}).get("overall_score") or 50), row) for row in rows]
    sortable.sort(key=lambda x: x[0], reverse=True)
    out = {}
    for n in (1, 3, 5, 10):
        top = [row for _, row in sortable[:n]]
        graded = [row for row in top if row.get("status") in {"CORRECT", "WRONG"}]
        correct = [row for row in graded if row.get("status") == "CORRECT"]
        out[f"top_{n}"] = {"graded": len(graded), "correct": len(correct), "accuracy": round(len(correct) / len(graded), 4) if graded else None}
    return out


def suggested_weight_adjustments(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    if len(rows) < 30:
        return {"sample_size": len(rows), "adjustments": [], "message": "Need at least 30 settled rows before suggesting weight changes."}
    return {"sample_size": len(rows), "adjustments": [], "message": "Weight adjustment engine scaffolded; feature-level settled attribution is required for safe recommendations."}


def model_version() -> Dict[str, Any]:
    return {
        "ok": True,
        "sport": "mlb",
        "model_version": MODEL_VERSION,
        "game_winner_model": mlb_game_winner_engine.MODEL_VERSION,
        "created_at": MODEL_CREATED_AT,
        "weights": WEIGHTS,
        "confidence_tiers": CONFIDENCE_TIERS,
        "probability_engine": "market-anchored logistic score with real-book EV promotion guardrails",
        "data_architecture": {
            "lambda": True,
            "api_gateway": True,
            "dynamodb": True,
            "s3_raw_archive": bool(RAW_ARCHIVE_BUCKET),
            "eventbridge_15_min": True,
            "daily_lock_t_minus_first_game": True,
        },
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
        limit = min(int(params.get("limit") or 80), 250)
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
