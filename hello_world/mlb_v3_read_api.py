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

MODEL_VERSION = "INQSI-MLB-v5.0-ranked-winner-v15.10-active-ensemble"
VERSION = "MLB-V3-READ-API-v6-ranked-winner-v15.10"


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
    ranked_version = (
        getattr(ENGINE, "MLB_RANKED_WINNER_VERSION", None)
        if ENGINE is not None
        else runtime.get("rankedWinnerVersion")
    )
    ranked_policy = (
        getattr(ENGINE, "MLB_RANKED_WINNER_POLICY_VERSION", None)
        if ENGINE is not None
        else runtime.get("rankedWinnerPolicyVersion")
    )
    ranked_ready = bool(
        ENGINE_IMPORT_OK
        and runtime.get("ok") is True
        and (runtime.get("steps") or {}).get("rankedWinnerV15_10SelectionInstalled") is True
        and ranked_version
    )
    return {
        "ok": ranked_ready,
        "sport": "mlb",
        "model_version": MODEL_VERSION,
        "primaryAlgorithm": ranked_version or MODEL_VERSION,
        "primaryAlgorithmActive": ranked_ready,
        "rankedWinnerPolicyVersion": ranked_policy,
        "rankedWinnerFirstSlateDateEt": runtime.get("rankedWinnerFirstSlateDate") or "2026-07-24",
        "precisionHitRateEvidencePassed": False,
        "allowedProductionOutput": ["PICK"],
        "productionSelectionAllowed": True,
        "automaticWagerAllowed": False,
        "legacyRecommendationAuthority": False,
        "legacyFallbackAllowed": False,
        "game_winner_model": getattr(ENGINE, "MODEL_VERSION", None) if ENGINE is not None else None,
        "game_winner_engine": getattr(ENGINE, "ENGINE", None) if ENGINE is not None else None,
        "gameWinnerDiagnosticRole": "active_ranked_model_direction_and_immutable_audit",
        "ml_optimization_version": OPTIMIZATION_VERSION,
        "ml_runtime_install": runtime,
        "engine_import_ok": ENGINE_IMPORT_OK,
        "engine_import_error": ENGINE_IMPORT_ERROR,
        "apiRuntimeVersion": VERSION,
        "pick_type": "individual_game_moneyline_ranked_pick",
        "requiredWinnerPickPolicy": "one active-model ranked winner PICK for every valid MLB game",
        "playablePolicy": "winner prediction is always returned; precision and trade qualification are separate",
        "mlDirectionPolicy": "active exported ensemble is sole direction authority; legacy selectors are diagnostic only",
        "mlReliabilityPolicy": "model probability is reported honestly; no 80-90% label is assigned without evidence",
        "productionAuthoritySource": "mlb_ranked_winner_v15_10_active_ensemble",
        "automaticPromotionPolicy": "winner model fixed for release; precision/trade promotion remains disabled",
        "legacyV1AuthorityEnabled": False,
        "awsNativeTrainingInstalled": True,
        "awsNativeTrainingAuthority": False,
        "awsNativeTrainingHealthSource": "separate_mode_specific_status_contract",
        "firstPromotionRequiresManualReview": True,
        "manualReviewCreatesShadowApprovalOnly": True,
        "v2InferenceConsumerInstalled": False,
        "runtimeAuthorityActivationAvailable": True,
        "parlaysEnabled": False,
        "readOnly": True,
        "sourcePolicy": "Canonical 15-minute market slots, exported active ensemble, immutable pre-lock snapshots, official FINAL labels, and separate precision/trade qualification.",
    }


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    event = event or {}
    if str(event.get("httpMethod") or "GET").upper() == "OPTIONS":
        return _response(200, {"ok": True})
    path = _path(event)
    params = _query(event)
    model = _model_body()
    if path == "/v1/mlb/model/version":
        return _response(200 if model.get("ok") is True else 503, model)
    if path not in {"/v1/mlb/today", "/v1/mlb/games", "/v1/mlb/predictions", "/v1/mlb/game-winners"}:
        return _response(404, {"ok": False, "error": "route_not_found", "path": path, "apiRuntimeVersion": VERSION})
    if ENGINE is None or model.get("ok") is not True:
        return _response(503, {**model, "ok": False, "winner_predictions": [], "predictions": [], "count": 0})
    date = params.get("game_date_et") or params.get("date") or _today_et()
    try:
        reader = getattr(ENGINE, "read_persisted_predictions", None)
        if not callable(reader):
            raise RuntimeError("persisted_prelock_prediction_reader_unavailable")
        result = reader(
            date,
            # This public Lambda has no write authority. Persisted candidates
            # are owned by protected scheduled ingestion.
            store=False,
            limit=min(max(int(params.get("limit") or 500), 1), 500),
        )
    except Exception as exc:
        return _response(500, {**_model_body(), "ok": False, "date": date, "error": str(exc), "winner_predictions": [], "predictions": [], "count": 0})
    result = dict(result or {})
    result.update({
        "sport": "mlb",
        "date": date,
        "model_version": MODEL_VERSION,
        "primaryAlgorithm": model.get("primaryAlgorithm"),
        "primaryAlgorithmActive": model.get("primaryAlgorithmActive"),
        "rankedWinnerPolicyVersion": model.get("rankedWinnerPolicyVersion"),
        "legacyRecommendationAuthority": False,
        "automaticWagerAllowed": False,
        "ml_runtime_install": model.get("ml_runtime_install"),
        "apiRuntimeVersion": VERSION,
        "winner_predictions": result.get("predictions") or [],
        "parlaysEnabled": False,
        "readOnly": True,
    })
    return _response(200, result)
