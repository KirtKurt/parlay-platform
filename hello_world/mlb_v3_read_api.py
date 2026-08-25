from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict
from zoneinfo import ZoneInfo

import mlb_ml_runtime_install_v3
import mlb_persisted_prelock_public_read_v1 as persisted_prelock_read
import mlb_terminal_lifecycle_count_reconciliation as lifecycle_counts

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
HISTORICAL_MODEL_VERSION = "INQSI-MLB-v5.1.1-historical-daily-only-cutover-wager-disabled"
VERSION = "MLB-V3-READ-API-v7-exact-persisted-prelock-public-read"
HISTORICAL_API_EXTENSION_VERSION = "MLB-V3-HISTORICAL-EXTENSION-v1.4-append-only-cutover-wager-disabled"


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
    # MLB_AUTO_R7_FAIL_CLOSED_AUTHORITY_V1
    return {
        "ok": False, "sport": "mlb",
        "status": "NO_QUALIFIED_CHAMPION", "error": "NO_QUALIFIED_CHAMPION",
        "publicationClosed": True, "productionSelectionAllowed": False,
        "model_version": None, "primaryAlgorithm": None, "primaryAlgorithmActive": False,
        "soleProductionAlgorithm": None, "game_winner_model": None,
        "requestedAuthority": "AWS_ML_PROSPECTIVE_R7",
        "qualifiedChampionRequired": True, "qualifiedChampionPresent": False,
        "r7ChampionQualified": False, "r7DeploymentIdentity": None,
        "legacyFallbackAllowed": False, "automaticLegacyRestoreAllowed": False,
        "legacyRecommendationAuthority": False, "retiredAuthoritySuppressed": True,
        "retiredV15_10Eligible": False, "automaticWagerAllowed": False,
        "rowLevelAutomaticWagerAllowed": False, "parlaysEnabled": False,
        "readOnly": True, "apiRuntimeVersion": VERSION,
        "authorityContractVersion": "MLB-AUTO-R7-QUALIFIED-CHAMPION-ONLY-v1",
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
        # The canonical lifecycle reader may return one placeholder per game
        # before T-45. Replace only those non-canonical placeholders with the
        # exact stored GAME row when its newest write-once PREGAME snapshot
        # validates byte-for-byte. No prediction is recalculated here.
        result = persisted_prelock_read.merge_into_payload(
            ENGINE,
            date,
            result or {},
        )
    except Exception as exc:
        return _response(500, {**_model_body(), "ok": False, "date": date, "error": str(exc), "winner_predictions": [], "predictions": [], "count": 0})
    result = dict(result or {})
    prelock_proof = dict(result.get("persistedPrelockPublicRead") or {})
    result = lifecycle_counts.reconcile_payload(
        result,
        row_field="predictions",
    )
    # Lifecycle reconciliation intentionally reports canonical T-45 coverage.
    # Preserve the separate pre-lock display-coverage truth established by the
    # exact persisted row/snapshot pair so deployment and clients do not confuse
    # an open lock with a missing prediction.
    if prelock_proof.get("coverageComplete") is True:
        displayed = int(prelock_proof.get("returnedWinnerPredictionCount") or 0)
        result["displayPredictionCount"] = displayed
        result["allGamesPredicted"] = True
        result["allGamesHaveDisplayedWinnerPrediction"] = True
        result["predictionCoverageComplete"] = True
        result["persistedPrelockPublicRead"] = prelock_proof
    result.update({
        "sport": "mlb",
        "date": date,
        "model_version": model.get("model_version"),
        "primaryAlgorithm": model.get("primaryAlgorithm"),
        "primaryAlgorithmActive": model.get("primaryAlgorithmActive"),
        "historicalDailyChampionActive": model.get("historicalDailyChampionActive"),
        "historicalProductionCutoverActive": model.get("historicalProductionCutoverActive"),
        "dailySlateAccuracyEvidencePassed": model.get("dailySlateAccuracyEvidencePassed"),
        "accuracyEvidenceScope": model.get("accuracyEvidenceScope"),
        "productionAuthoritySource": model.get("productionAuthoritySource"),
        "legacyAlgorithmAuthorityDisabled": model.get("legacyAlgorithmAuthorityDisabled"),
        "incumbentProductionAuthorityDestroyed": model.get("incumbentProductionAuthorityDestroyed"),
        "legacyFallbackAllowed": False,
        "automaticLegacyRestoreAllowed": False,
        "soleProductionAlgorithm": model.get("soleProductionAlgorithm"),
        "rankedWinnerPolicyVersion": model.get("rankedWinnerPolicyVersion"),
        "legacyRecommendationAuthority": False,
        "automaticWagerAllowed": False,
        "predictionOnlyWagerSafetyInstalled": model.get(
            "predictionOnlyWagerSafetyInstalled"
        ),
        "rowLevelAutomaticWagerAllowed": False,
        "ml_runtime_install": model.get("ml_runtime_install"),
        "apiRuntimeVersion": VERSION,
        "persistedPrelockPublicReadVersion": persisted_prelock_read.VERSION,
        "winner_predictions": result.get("predictions") or [],
        "parlaysEnabled": False,
        "readOnly": True,
    })
    return _response(200, result)
