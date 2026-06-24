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

MODEL_VERSION = "INQSI-MLB-v1.0-core"
MODEL_CREATED_AT = "2026-06-24"
MINIMUM_PULLS = 2

WEIGHTS = {
    "starting_pitcher": 0.20,
    "bullpen_freshness": 0.15,
    "lineup_strength": 0.15,
    "offensive_splits": 0.10,
    "park_weather": 0.10,
    "injuries_news": 0.10,
    "travel_rest": 0.07,
    "defense": 0.05,
    "market_movement": 0.05,
    "model_edge": 0.03,
}

CONFIDENCE_TIERS = [
    (0.67, "Premium"),
    (0.60, "Solid"),
    (0.55, "Lean"),
    (0.50, "Coin Flip"),
    (0.00, "Pass"),
]

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


def _component_score(row: Dict[str, Any], component: str) -> Dict[str, Any]:
    context = row.get("advanced_context") or {}
    advanced = context.get("advanced_eligibility") or {}
    blockers = set(advanced.get("blocked_missing_or_pending") or [])

    if component == "market_movement":
        hot_delta = abs(float(row.get("hot_delta") or 0))
        agreement = row.get("book_agreement") or {}
        agree = int(agreement.get("agreeing_books") or 0)
        disagree = int(agreement.get("disagreeing_books") or 0)
        raw = min(100.0, 50.0 + hot_delta * 1500.0 + agree * 3.0 - disagree * 4.0)
        return {"score": max(0.0, raw), "source_status": "CONNECTED", "blocker": None}

    blocker_map = {
        "starting_pitcher": "fip_xfip",
        "bullpen_freshness": "bullpen_fatigue",
        "lineup_strength": "confirmed_lineups",
        "offensive_splits": "wrc_plus",
        "park_weather": "weather_wind_roof",
        "injuries_news": "injuries_late_scratches_news",
        "travel_rest": "travel_rest",
        "defense": "defense",
        "model_edge": "closing_line_value",
    }
    blocker = blocker_map.get(component)
    if blocker in blockers or blocker in {"travel_rest", "defense"}:
        return {"score": 50.0, "source_status": "PENDING_SOURCE", "blocker": blocker}
    return {"score": 50.0, "source_status": "CONNECTED_NEUTRAL_UNTIL_CALIBRATED", "blocker": None}


def score_prediction_row(row: Dict[str, Any]) -> Dict[str, Any]:
    components = {}
    weighted = 0.0
    total_weight = 0.0
    blockers = set(row.get("advanced_blockers") or [])
    for component, weight in WEIGHTS.items():
        payload = _component_score(row, component)
        components[component] = {**payload, "weight": weight}
        weighted += float(payload["score"]) * weight
        total_weight += weight
        if payload.get("blocker"):
            blockers.add(payload["blocker"])

    score = weighted / total_weight if total_weight else 50.0
    score_diff = score - 50.0
    prob = probability_from_score(score_diff)
    return {
        "model_version": MODEL_VERSION,
        "overall_score": round(score, 3),
        "score_diff": round(score_diff, 3),
        "win_probability": round(prob, 4),
        "confidence_tier": confidence_tier(prob),
        "advanced_eligible": len(blockers) == 0,
        "advanced_blockers": sorted(blockers),
        "components": components,
        "weights": WEIGHTS,
    }


def today(game_date: Optional[str] = None) -> Dict[str, Any]:
    game_date = game_date or _today_et()
    return {
        "ok": True,
        "sport": "mlb",
        "date": game_date,
        "model_version": MODEL_VERSION,
        "source_status": source_status(),
        "message": "INQSI MLB v1.0 core is available. Premium eligibility depends on connected advanced data sources.",
    }


def games(game_date: Optional[str] = None, limit: int = 80) -> Dict[str, Any]:
    game_date = game_date or _today_et()
    data = hot_sides(game_date=game_date, limit=limit, store=False, include_no_edge=True)
    return {
        "ok": True,
        "sport": "mlb",
        "date": game_date,
        "model_version": MODEL_VERSION,
        "count": len(data.get("game_predictions") or []),
        "games": data.get("game_predictions") or [],
        "pull_mode": data.get("pull_mode"),
    }


def predictions(game_date: Optional[str] = None, limit: int = 80, store: bool = False) -> Dict[str, Any]:
    game_date = game_date or _today_et()
    data = hot_sides(game_date=game_date, limit=limit, store=store, include_no_edge=True)
    rows = []
    for row in data.get("game_predictions") or []:
        scored = score_prediction_row(row)
        enriched = {**row, "inqsi_v1_score": scored}
        rows.append(enriched)
    rows.sort(key=lambda r: (r.get("inqsi_v1_score") or {}).get("overall_score", 0), reverse=True)
    return {
        "ok": True,
        "sport": "mlb",
        "date": game_date,
        "model_version": MODEL_VERSION,
        "count": len(rows),
        "predictions": rows,
        "parlay_analysis": parlay_analysis(rows, data.get("three_leg_parlay") or {}),
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
    sortable = []
    for row in rows:
        score_payload = row.get("inqsi_v1_score") or {}
        sortable.append((float(score_payload.get("overall_score") or 50), row))
    sortable.sort(key=lambda x: x[0], reverse=True)
    out = {}
    for n in (1, 3, 5, 10):
        top = [row for _, row in sortable[:n]]
        graded = [row for row in top if row.get("status") in {"CORRECT", "WRONG"}]
        correct = [row for row in graded if row.get("status") == "CORRECT"]
        out[f"top_{n}"] = {"graded": len(graded), "correct": len(correct), "accuracy": round(len(correct) / len(graded), 4) if graded else None}
    return out


def parlay_analysis(rows: List[Dict[str, Any]], parlay: Dict[str, Any]) -> Dict[str, Any]:
    blockers = {}
    for row in rows:
        for blocker in (row.get("inqsi_v1_score") or {}).get("advanced_blockers") or []:
            blockers[blocker] = blockers.get(blocker, 0) + 1
    eligible = [row for row in rows if (row.get("inqsi_v1_score") or {}).get("advanced_eligible")]
    return {
        "model_version": MODEL_VERSION,
        "advanced_eligible_legs": len(eligible),
        "blockers": blockers,
        "suggested_mode": "NO_ADVANCED_PARLAY" if len(eligible) < 3 else "ADVANCED_PARLAY_CANDIDATE",
        "three_leg_parlay": parlay,
        "notes": [
            "Do not force a 3-leg MLB parlay unless at least 3 legs are advanced-eligible or market-only mode is explicitly requested.",
            "Market-only research rows remain available for settlement learning.",
        ],
    }


def suggested_weight_adjustments(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    if len(rows) < 30:
        return {"sample_size": len(rows), "adjustments": [], "message": "Need at least 30 settled rows before suggesting weight changes."}
    return {"sample_size": len(rows), "adjustments": [], "message": "Weight adjustment engine scaffolded; feature-level settled attribution is required for safe recommendations."}


def model_version() -> Dict[str, Any]:
    return {
        "ok": True,
        "sport": "mlb",
        "model_version": MODEL_VERSION,
        "created_at": MODEL_CREATED_AT,
        "weights": WEIGHTS,
        "confidence_tiers": CONFIDENCE_TIERS,
        "probability_engine": "logistic(score_diff, k=0.075)",
        "data_architecture": {
            "lambda": True,
            "api_gateway": True,
            "dynamodb": True,
            "s3_raw_archive": bool(RAW_ARCHIVE_BUCKET),
            "eventbridge_15_min": True,
        },
    }


def lambda_handler(event, context):
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
        if path.endswith("/predictions"):
            return _resp(200, predictions(game_date, limit, params.get("store", "false").lower() == "true"))
        if path.endswith("/audit"):
            return _resp(200, audit(game_date))
        if path.endswith("/model/version"):
            return _resp(200, model_version())
        return _resp(404, {"ok": False, "error": f"Route not found: {method} {path}"})
    except Exception as exc:
        return _resp(500, {"ok": False, "sport": "mlb", "error": str(exc)})
