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
HISTORICAL_MODEL_VERSION = "INQSI-MLB-v5.1.1-historical-daily-only-cutover-wager-disabled"
VERSION = "MLB-V3-READ-API-v6-ranked-winner-v15.10"
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
    runtime = getattr(ENGINE, "MLB_ML_RUNTIME_INSTALL_V3", RUNTIME_INSTALL) if ENGINE is not None else RUNTIME_INSTALL
    steps = runtime.get("steps") or {}
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
    historical_version = runtime.get("historicalDailyPolicyVersion")
    historical_active = runtime.get("historicalDailyChampionActive") is True
    cutover_active = runtime.get("historicalProductionCutoverActive") is True
    authority_coherent = steps.get("historicalAuthorityStateCoherent") is True
    runtime_ready = bool(
        ENGINE_IMPORT_OK
        and runtime.get("ok") is True
        and steps.get("rankedWinnerV15_10SelectionInstalled") is True
        and runtime.get("historicalDailyChampionOutermostAuthorityInstalled") is True
        and authority_coherent
        and ranked_version
    )
    primary = historical_version if historical_active else (ranked_version or MODEL_VERSION)
    authority_source = runtime.get("productionAuthoritySource") or (
        "mlb_historical_daily_champion_only"
        if historical_active
        else "mlb_ranked_winner_v15_10_active_ensemble"
    )
    return {
        "ok": runtime_ready,
        "sport": "mlb",
        "model_version": HISTORICAL_MODEL_VERSION if historical_active else MODEL_VERSION,
        "primaryAlgorithm": primary,
        "primaryAlgorithmActive": runtime_ready,
        "historicalDailyChampionActive": historical_active,
        "historicalDailyPolicyVersion": historical_version,
        "historicalRuntimeExtensionVersion": runtime.get("historicalRuntimeExtensionVersion"),
        "historicalApiExtensionVersion": HISTORICAL_API_EXTENSION_VERSION,
        "historicalDailyChampionLoadStatus": runtime.get("historicalDailyChampionLoadStatus"),
        "historicalDailyPromotionGateVersion": runtime.get("historicalDailyPromotionGateVersion"),
        "historicalProductionCutoverActive": cutover_active,
        "historicalProductionCutoverStatus": runtime.get("historicalProductionCutoverStatus"),
        "historicalProductionCutoverVersion": runtime.get("historicalProductionCutoverVersion"),
        "historicalAuthorityStateCoherent": authority_coherent,
        "incumbentAlgorithm": ranked_version,
        "incumbentRole": (
            "quarantined_feature_and_explicit_rollback_artifact_only"
            if cutover_active
            else "active_until_historical_gate"
        ),
        "rankedWinnerPolicyVersion": ranked_policy,
        "rankedWinnerFirstSlateDateEt": runtime.get("rankedWinnerFirstSlateDate") or "2026-07-24",
        "precisionHitRateEvidencePassed": historical_active,
        "dailySlateAccuracyEvidencePassed": historical_active,
        "dailySlateAccuracyRequirement": 0.80,
        "dailySlateAccuracyTargetHigh": 0.90,
        "accuracyEvidenceScope": "complete_day_slate_not_individual_game",
        "requiredEvidencePartitions": {
            "trainingGames": 1000,
            "walkForwardGames": 200,
            "untouchedAuditGames": 200,
        },
        "allowedProductionOutput": ["PICK"],
        "productionSelectionAllowed": runtime_ready,
        "automaticWagerAllowed": False,
        "predictionOnlyWagerSafetyInstalled": runtime.get(
            "predictionOnlyWagerSafetyInstalled"
        ) is True,
        "rowLevelAutomaticWagerAllowed": False,
        "legacyRecommendationAuthority": False,
        "legacyAlgorithmAuthorityDisabled": cutover_active,
        "incumbentProductionAuthorityDestroyed": cutover_active,
        "legacyFallbackAllowed": False,
        "automaticLegacyRestoreAllowed": False,
        "soleProductionAlgorithm": primary,
        "game_winner_model": getattr(ENGINE, "MODEL_VERSION", None) if ENGINE is not None else None,
        "game_winner_engine": getattr(ENGINE, "ENGINE", None) if ENGINE is not None else None,
        "gameWinnerDiagnosticRole": (
            "historical_daily_champion_direction_and_immutable_audit"
            if historical_active
            else "active_ranked_model_direction_and_immutable_audit"
        ),
        "ml_optimization_version": OPTIMIZATION_VERSION,
        "ml_runtime_install": runtime,
        "engine_import_ok": ENGINE_IMPORT_OK,
        "engine_import_error": ENGINE_IMPORT_ERROR,
        "apiRuntimeVersion": VERSION,
        "pick_type": (
            "individual_game_moneyline_pick_evaluated_as_complete_daily_slate"
            if historical_active
            else "individual_game_moneyline_ranked_pick"
        ),
        "requiredWinnerPickPolicy": (
            "one winner PICK for every valid MLB game on the complete slate"
            if historical_active
            else "one active-model ranked winner PICK for every valid MLB game"
        ),
        "playablePolicy": (
            "winner predictions cover the slate; the 80 percent requirement is evaluated by day, not assigned per game"
            if historical_active
            else "winner prediction is always returned; precision and trade qualification are separate"
        ),
        "mlDirectionPolicy": (
            "the historical daily champion is the sole outermost direction authority; the prior selector is quarantined and has no automatic fallback path"
            if historical_active
            else "active exported ensemble is sole direction authority until the immutable historical daily gate passes"
        ),
        "mlReliabilityPolicy": (
            "the 80 percent requirement applies only to complete-day held-out slate accuracy, never to an individual game label"
            if historical_active
            else "model probability is reported honestly; no 80-90% label is assigned without evidence"
        ),
        "productionAuthoritySource": authority_source,
        "productionAuthorityLifecycleState": runtime.get("productionAuthorityLifecycleState") or (
            "HISTORICAL_DAILY_ONLY" if historical_active else "INCUMBENT_UNTIL_HISTORICAL_GATE"
        ),
        "automaticPromotionPolicy": (
            "automatic atomic fail-closed champion plus historical-only cutover after the immutable 1000/200/200 every-day gate passes"
            if historical_active
            else "automatic promotion remains blocked until the immutable full evidence gate passes"
        ),
        "legacyV1AuthorityEnabled": False,
        "awsNativeTrainingInstalled": True,
        "awsNativeTrainingAuthority": historical_active,
        "awsNativeTrainingHealthSource": (
            "mlb_historical_optimizer_status_and_versioned_champion_artifact"
            if historical_active
            else "separate_mode_specific_status_contract"
        ),
        "firstPromotionRequiresManualReview": False,
        "manualReviewCreatesShadowApprovalOnly": False,
        "v2InferenceConsumerInstalled": historical_active,
        "runtimeAuthorityActivationAvailable": True,
        "parlaysEnabled": False,
        "readOnly": True,
        "sourcePolicy": (
            "The Odds API historical 15-minute snapshots from 01:00 ET, per-game T-45 clipping, immutable complete-slate datasets, official FINAL labels, chronological whole-day partitions, and an untouched audit."
            if historical_active
            else "Canonical 15-minute market slots, exported active ensemble, immutable pre-lock snapshots, official FINAL labels, and separate precision/trade qualification."
        ),
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
        "winner_predictions": result.get("predictions") or [],
        "parlaysEnabled": False,
        "readOnly": True,
    })
    return _response(200, result)
