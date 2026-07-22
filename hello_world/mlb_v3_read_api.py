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

try:
    import mlb_scoring_run_proof as SCORING_PROOF
    SCORING_PROOF_IMPORT_OK = True
    SCORING_PROOF_IMPORT_ERROR = None
except Exception as exc:
    SCORING_PROOF = None
    SCORING_PROOF_IMPORT_OK = False
    SCORING_PROOF_IMPORT_ERROR = str(exc)

MODEL_VERSION = "INQSI-MLB-v4.0-canonical-probability-aws-v2-shadow-manual-first"
VERSION = "MLB-V3-READ-API-v4.1-persisted-canonical-scoring-proof"


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


def _scoring_status(date: str) -> Dict[str, Any]:
    if SCORING_PROOF is None:
        return {
            "ok": False,
            "sport": "mlb",
            "gameDateEt": date,
            "proof": None,
            "error": f"scoring_proof_reader_unavailable:{SCORING_PROOF_IMPORT_ERROR}",
        }
    try:
        return SCORING_PROOF.latest_proof(date)
    except Exception as exc:
        return {
            "ok": False,
            "sport": "mlb",
            "gameDateEt": date,
            "proof": None,
            "error": f"scoring_proof_read_failed:{type(exc).__name__}:{exc}",
        }


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
        "scoringProofReadAvailable": SCORING_PROOF_IMPORT_OK,
        "scoringProofVersion": getattr(SCORING_PROOF, "VERSION", None),
        "scoringProofImportError": SCORING_PROOF_IMPORT_ERROR,
        "pick_type": "individual_game_moneyline",
        "requiredWinnerPickPolicy": "one_official_locked_winner_prediction_for_every_mlb_game",
        "playablePolicy": "playability_is_separate_and_may_be_false_for_an_official_prediction",
        "mlDirectionPolicy": "persisted_rules_market_direction_v2_shadow_only_no_v2_runtime_consumer",
        "mlReliabilityPolicy": "reliability_probability_is_never_team_win_probability",
        "productionAuthoritySource": "persisted_canonical_rules_market_prediction_v2_shadow_only",
        "automaticPromotionPolicy": "disabled_manual_review_creates_shadow_pointer_only",
        "legacyV1AuthorityEnabled": False,
        "awsNativeTrainingInstalled": True,
        "awsNativeTrainingAuthority": False,
        "awsNativeTrainingHealthSource": "separate_mode_specific_status_contract",
        "firstPromotionRequiresManualReview": True,
        "manualReviewCreatesShadowApprovalOnly": True,
        "v2InferenceConsumerInstalled": False,
        "runtimeAuthorityActivationAvailable": False,
        "parlaysEnabled": False,
        "readOnly": True,
        "sourcePolicy": "Canonical 15-minute market slots plus immutable pre-lock Fundamentals V2 snapshots and official FINAL labels.",
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
    date = params.get("game_date_et") or params.get("date") or _today_et()
    scoring_status = _scoring_status(date)
    if ENGINE is None or model.get("ok") is not True:
        return _response(503, {
            **model,
            "ok": False,
            "date": date,
            "scoringRunProofStatus": scoring_status,
            "scoring_run_proof": scoring_status.get("proof"),
            "winner_predictions": [],
            "predictions": [],
            "count": 0,
        })
    try:
        reader = getattr(ENGINE, "read_persisted_predictions", None)
        if not callable(reader):
            raise RuntimeError("persisted_prelock_prediction_reader_unavailable")
        result = reader(
            date,
            # This public Lambda has no write authority. Persisted
            # candidates are owned by protected scheduled ingestion.
            store=False,
            limit=min(max(int(params.get("limit") or 500), 1), 500),
        )
    except Exception as exc:
        return _response(500, {
            **_model_body(),
            "ok": False,
            "date": date,
            "error": str(exc),
            "scoringRunProofStatus": scoring_status,
            "scoring_run_proof": scoring_status.get("proof"),
            "winner_predictions": [],
            "predictions": [],
            "count": 0,
        })
    result = dict(result or {})
    result.update({
        "sport": "mlb",
        "date": date,
        "model_version": MODEL_VERSION,
        "ml_runtime_install": model.get("ml_runtime_install"),
        "apiRuntimeVersion": VERSION,
        "winner_predictions": result.get("predictions") or [],
        "scoringRunProofStatus": scoring_status,
        "scoring_run_proof": scoring_status.get("proof"),
        "scoringProofComplete": bool(
            scoring_status.get("ok") is True
            and isinstance(scoring_status.get("proof"), dict)
            and scoring_status["proof"].get("status") == "PASS"
        ),
        "parlaysEnabled": False,
        "readOnly": True,
    })
    return _response(200, result)
