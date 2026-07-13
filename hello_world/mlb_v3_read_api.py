from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict
from zoneinfo import ZoneInfo

import mlb_ml_runtime_install_v3

RUNTIME_INSTALL = mlb_ml_runtime_install_v3.install()

try:
    import mlb_game_winner_engine as ENGINE
    ENGINE_IMPORT_OK = True
    ENGINE_IMPORT_ERROR = None
except Exception as exc:
    ENGINE = None
    ENGINE_IMPORT_OK = False
    ENGINE_IMPORT_ERROR = str(exc)

try:
    import mlb_ml_optimization_v3 as OPTIMIZATION
    OPTIMIZATION_VERSION = OPTIMIZATION.VERSION
except Exception:
    OPTIMIZATION_VERSION = None

MODEL_VERSION = "INQSI-MLB-v3.2-80pct-production-60pct-game-lock"
VERSION = "MLB-V3-READ-API-v3-80pct-production-60pct-game-lock"


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return str(value)


def _response(status: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json",
            "access-control-allow-origin": "*",
            "access-control-allow-headers": "content-type,authorization,x-inqsi-admin-token",
            "access-control-allow-methods": "GET,OPTIONS",
            "cache-control": "no-store",
        },
        "body": json.dumps(body, default=_json_default),
    }


def _path(event: Dict[str, Any]) -> str:
    return ((event or {}).get("rawPath") or (event or {}).get("path") or "/").rstrip("/") or "/"


def _query(event: Dict[str, Any]) -> Dict[str, str]:
    return (event or {}).get("queryStringParameters") or {}


def _today_et() -> str:
    return datetime.now(ZoneInfo("America/New_York")).date().isoformat()


def _model_body() -> Dict[str, Any]:
    runtime = getattr(ENGINE, "MLB_ML_RUNTIME_INSTALL_V3", RUNTIME_INSTALL) if ENGINE is not None else RUNTIME_INSTALL
    return {
        "ok": bool(ENGINE_IMPORT_OK and runtime.get("ok") is True),
        "sport": "mlb",
        "model_version": MODEL_VERSION,
        "game_winner_model": getattr(ENGINE, "MODEL_VERSION", None) if ENGINE is not None else None,
        "game_winner_engine": getattr(ENGINE, "ENGINE", None) if ENGINE is not None else None,
        "ml_optimization_version": OPTIMIZATION_VERSION,
        "ml_runtime_install": runtime,
        "engine_import_ok": ENGINE_IMPORT_OK,
        "engine_import_error": ENGINE_IMPORT_ERROR,
        "apiRuntimeVersion": VERSION,
        "pick_type": "individual_game_moneyline",
        "requiredWinnerPickPolicy": "one_visible_winner_prediction_for_every_mlb_game; official_lock_requires_60pct_or_higher",
        "playablePolicy": "playability_is_separate_and_may_be_false_for_an_official_prediction",
        "mlDirectionPolicy": "outcome_model_requires_80pct_untouched_accuracy_and_80pct_rolling_official_card_slate_accuracy_before_automatic_authority",
        "mlReliabilityPolicy": "selected_playable_reliability_requires_80pct_untouched_accuracy; reliability_probability_is_never_team_win_probability",
        "productionAuthoritySource": "gate_promoted_DynamoDB_champion_bundle_only",
        "automaticPromotionPolicy": "authoritative_AWS_audit_only_after_independent_80pct_authority_gates",
        "rolling24hAccuracyTargetPct": 80.0,
        "outcomeUntouchedAccuracyTargetPct": 80.0,
        "playableReliabilityTargetPct": 80.0,
        "exactLockedOddsCoverageTargetPct": 80.0,
        "individualGameLockMinimumProbabilityPct": 60.0,
        "below60PctRowsRemainVisibleDiagnostics": True,
        "parlaysEnabled": False,
        "sourcePolicy": "The Odds API pull history plus timestamped source-honest fundamentals snapshots.",
    }


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    event = event or {}
    if str(event.get("httpMethod") or "GET").upper() == "OPTIONS":
        return _response(200, {"ok": True})
    path = _path(event)
    params = _query(event)
    if path == "/v1/mlb/model/version":
        return _response(200, _model_body())
    if path not in {"/v1/mlb/today", "/v1/mlb/games", "/v1/mlb/predictions", "/v1/mlb/game-winners"}:
        return _response(404, {"ok": False, "error": "route_not_found", "path": path, "apiRuntimeVersion": VERSION})
    if ENGINE is None:
        return _response(503, {**_model_body(), "ok": False, "winner_predictions": [], "predictions": [], "count": 0})
    date = params.get("game_date_et") or params.get("date") or _today_et()
    try:
        result = ENGINE.predict_all(
            date,
            store=str(params.get("store") or "false").lower() == "true",
            limit=min(max(int(params.get("limit") or 500), 1), 500),
        )
    except Exception as exc:
        return _response(500, {**_model_body(), "ok": False, "date": date, "error": str(exc), "winner_predictions": [], "predictions": [], "count": 0})
    result = dict(result or {})
    result.update({
        "sport": "mlb",
        "date": date,
        "model_version": MODEL_VERSION,
        "ml_runtime_install": _model_body().get("ml_runtime_install"),
        "apiRuntimeVersion": VERSION,
        "winner_predictions": result.get("predictions") or [],
        "parlaysEnabled": False,
    })
    return _response(200, result)
