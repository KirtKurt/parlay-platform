from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

MODEL_VERSION = "INQSI-MLB-v5.0-ranked-winner-v15.10-active-ensemble"
MODEL_CREATED_AT = "2026-07-24"
PRIMARY_ALGORITHM = "INQSI-MLB-RANKED-WINNER-v15.10.0-active-ensemble"
POLICY_VERSION = "2026-07-24-mlb-ranked-winner-primary-v1"


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
            "cache-control": "no-store",
        },
        "body": json.dumps(body, default=_json_default),
    }


def _params(event: Dict[str, Any]) -> Dict[str, str]:
    return event.get("queryStringParameters") or {}


def _engine():
    import mlb_game_winner_engine
    return mlb_game_winner_engine


def _runtime() -> Dict[str, Any]:
    engine = _engine()
    value = getattr(engine, "MLB_ML_RUNTIME_INSTALL_V3", None)
    return value if isinstance(value, dict) else {}


def _engine_version() -> Dict[str, Any]:
    try:
        engine = _engine()
        runtime = _runtime()
        return {
            "ok": runtime.get("ok") is True,
            "engine": getattr(engine, "ENGINE", "unknown"),
            "modelVersion": getattr(engine, "MODEL_VERSION", "unknown"),
            "primaryAlgorithm": getattr(engine, "MLB_RANKED_WINNER_VERSION", None),
            "policyVersion": getattr(engine, "MLB_RANKED_WINNER_POLICY_VERSION", None),
            "runtime": runtime,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "engine": None, "modelVersion": None}


def _authority_fields() -> Dict[str, Any]:
    return {
        "model_version": MODEL_VERSION,
        "primaryAlgorithm": PRIMARY_ALGORITHM,
        "primaryAlgorithmActive": True,
        "rankedWinnerPolicyVersion": POLICY_VERSION,
        "productionAuthoritySource": "mlb_ranked_winner_v15_10_active_ensemble",
        "allowedProductionOutput": ["PICK"],
        "winnerPickRequiredForEveryValidEvent": True,
        "precisionQualificationSeparateFromPick": True,
        "precisionHitRateEvidencePassed": False,
        "legacyRecommendationAuthority": False,
        "legacyFallbackAllowed": False,
        "automaticWagerAllowed": False,
        "productionTradeAllowed": False,
    }


def model_version() -> Dict[str, Any]:
    engine_info = _engine_version()
    runtime = engine_info.get("runtime") or {}
    ready = bool(
        engine_info.get("ok") is True
        and engine_info.get("primaryAlgorithm") == PRIMARY_ALGORITHM
        and (runtime.get("steps") or {}).get("rankedWinnerV15_10SelectionInstalled") is True
    )
    return {
        "ok": ready,
        "sport": "mlb",
        **_authority_fields(),
        "primaryAlgorithmActive": ready,
        "game_winner_model": engine_info.get("modelVersion"),
        "game_winner_engine": engine_info.get("engine"),
        "engine_import_ok": engine_info.get("ok"),
        "engine_import_error": engine_info.get("error"),
        "ml_runtime_install": runtime,
        "created_at": MODEL_CREATED_AT,
        "pick_type": "individual_game_moneyline_ranked_pick",
        "requiredWinnerPickPolicy": "one active-model ranked winner PICK for every valid MLB game",
        "playablePolicy": "winner prediction is always returned; precision and trade qualification are separate",
        "parlaysEnabled": False,
        "sourcePolicy": "Persisted canonical predictions from the active exported ensemble, unique 15-minute market slots and immutable pre-lock evidence.",
        "data_architecture": {
            "lambda": True,
            "api_gateway": True,
            "dynamodb": True,
            "eventbridge_15_min": True,
            "per_game_immutable_t_minus_45_lock": True,
        },
    }


def today(game_date: Optional[str] = None) -> Dict[str, Any]:
    game_date = game_date or _today_et()
    status = model_version()
    return {
        "ok": status.get("ok") is True,
        "sport": "mlb",
        "date": game_date,
        **_authority_fields(),
        "primaryAlgorithmActive": status.get("primaryAlgorithmActive"),
        "priority": "one_ranked_winner_pick_per_valid_game",
        "parlaysEnabled": False,
        "message": "The active MLB V15.10 ensemble returns one winner pick for every valid game; precision and automatic wagering remain separate and disabled.",
    }


def predictions(game_date: Optional[str] = None, limit: int = 500, store: bool = False) -> Dict[str, Any]:
    game_date = game_date or _today_et()
    try:
        engine = _engine()
        runtime = getattr(engine, "MLB_ML_RUNTIME_INSTALL_V3", None)
        if not isinstance(runtime, dict) or runtime.get("ok") is not True:
            raise RuntimeError("MLB_PUBLIC_READ_RUNTIME_NOT_READY")
        if (runtime.get("steps") or {}).get("rankedWinnerV15_10SelectionInstalled") is not True:
            raise RuntimeError("MLB_RANKED_WINNER_V15_10_NOT_INSTALLED")
        reader = getattr(engine, "read_persisted_predictions", None)
        if not callable(reader):
            raise RuntimeError("persisted_prelock_prediction_reader_unavailable")
        winners = reader(game_date, store=False, limit=limit)
    except Exception as exc:
        return {
            "ok": False,
            "sport": "mlb",
            "date": game_date,
            **_authority_fields(),
            "error": str(exc),
            "winner_predictions": [],
            "count": 0,
        }
    rows = winners.get("predictions") or []
    return {
        "ok": True,
        "sport": "mlb",
        "date": game_date,
        **_authority_fields(),
        "game_winner_model": winners.get("modelVersion"),
        "priority": "one_ranked_winner_pick_per_valid_game",
        "parlaysEnabled": False,
        "count": winners.get("count", len(rows)),
        "productionSelectionCount": len([row for row in rows if row.get("selectionStatus") == "PICK"]),
        "precisionQualifiedCount": len([row for row in rows if row.get("precisionQualified") is True]),
        "pullCount": winners.get("pullCount"),
        "latestPullAt": winners.get("latestPullAt"),
        "winner_predictions": rows,
        "readOnly": True,
        "storage": {
            "requested": False,
            "callerRequestedWriteIgnored": bool(store),
            "gameWinnerStoredCount": 0,
        },
        "parlay_analysis": {"enabled": False, "reason": "MLB production is individual ranked game picks only."},
        "three_leg_parlay": {"ok": False, "disabled": True, "reason": "MLB production is individual ranked game picks only."},
    }


def audit(game_date: Optional[str] = None) -> Dict[str, Any]:
    game_date = game_date or _today_et()
    return {
        "ok": True,
        "sport": "mlb",
        "date": game_date,
        **_authority_fields(),
        "message": "Use /v1/mlb/game-winners for active V15.10 picks and settled-results endpoints for grading.",
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
            payload = model_version()
            return _resp(200 if payload.get("ok") is True else 503, payload)
        if path.endswith("/today"):
            payload = today(game_date)
            return _resp(200 if payload.get("ok") is True else 503, payload)
        if path.endswith("/games") or path.endswith("/predictions") or path.endswith("/game-winners"):
            payload = predictions(game_date, limit, False)
            return _resp(200 if payload.get("ok") is True else 503, payload)
        if path.endswith("/audit"):
            return _resp(200, audit(game_date))
        return _resp(404, {"ok": False, "error": f"Route not found: {method} {path}"})
    except Exception as exc:
        return _resp(500, {"ok": False, "sport": "mlb", **_authority_fields(), "error": str(exc)})


def lambda_handler(event, context):
    return handle(event, context)
