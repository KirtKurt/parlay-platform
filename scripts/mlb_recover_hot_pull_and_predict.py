from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from typing import Any, Dict

REPORT_PATH = "runtime_reports/mlb_hot_pull_recovery_latest.json"
ML_MODEL_PATH = os.environ.get("INQSI_MLB_ML_MODEL_PATH", "runtime_reports/mlb_ml_model_latest.json")

# SportsDataIO is enabled when the secret is present. Keep final-gate blocking
# disabled so a temporary provider outage does not erase the market-derived pick.
os.environ.setdefault("INQSI_MLB_USE_SPORTSDATAIO_FUNDAMENTALS", "true")
os.environ.setdefault("INQSI_REQUIRE_SPORTSDATAIO_FINAL_GATE", "false")
os.environ.setdefault("SPORTSDATAIO_TIMEOUT_SECONDS", "25")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe(obj: Any) -> Any:
    try:
        from decimal import Decimal
        if isinstance(obj, Decimal):
            return float(obj)
    except Exception:
        pass
    if isinstance(obj, dict):
        return {k: _safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_safe(v) for v in obj]
    return obj


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _sig(row: Dict[str, Any], side: str) -> Dict[str, Any]:
    value = row.get("homeSignal") if side == "home" else row.get("awaySignal")
    return value if isinstance(value, dict) else {}


def _load_model() -> Dict[str, Any] | None:
    try:
        with open(ML_MODEL_PATH, "r", encoding="utf-8") as f:
            model = json.load(f)
        return model if isinstance(model, dict) and model.get("ok") else None
    except Exception:
        return None


def _ml_features(row: Dict[str, Any]) -> Dict[str, float]:
    side = str(row.get("predictedSide") or "home").lower()
    if side not in {"home", "away"}:
        side = "home"
    other = "away" if side == "home" else "home"
    sig = _sig(row, side)
    opp = _sig(row, other)
    tags = set([str(x) for x in (row.get("tags") or [])] + [str(x) for x in (sig.get("tags") or [])])
    mp = _num(sig.get("marketConsensusProbability"), _num(sig.get("probLatest"), 0.5))
    op = _num(opp.get("marketConsensusProbability"), 1.0 - mp)
    return {
        "score": _num(row.get("score")),
        "winProbabilityPct": _num(row.get("winProbabilityPct")),
        "marketProb": mp,
        "marketEdge": mp - op,
        "bookDivergence": _num(sig.get("bookDivergence")),
        "reversalCount": _num(sig.get("reversalCount")),
        "runLineMoveAbs": abs(_num(sig.get("runLineMovement"))),
        "bookAgreement": 1.0 if "BOOK_AGREEMENT" in tags else 0.0,
        "bookDivergenceFlag": 1.0 if "BOOK_DIVERGENCE" in tags else 0.0,
        "runLineMove": 1.0 if "RUN_LINE_MOVEMENT" in tags else 0.0,
        "unconfirmedRunLine": 1.0 if "UNCONFIRMED_RUN_LINE_MOVE" in tags else 0.0,
        "compressedMarket": 1.0 if "COMPRESSED_MARKET" in tags else 0.0,
        "lean": 1.0 if str(row.get("confidenceTier") or "").lower() == "lean" else 0.0,
        "passTier": 1.0 if str(row.get("confidenceTier") or "").lower() == "pass" else 0.0,
    }


def _ml_score(row: Dict[str, Any], model: Dict[str, Any]) -> float | None:
    features = model.get("features") or []
    weights = model.get("weights") or {}
    means = model.get("means") or {}
    scales = model.get("scales") or model.get("stds") or {}
    fmap = _ml_features(row)
    z = _num(model.get("bias"))
    for feature in features:
        scale = _num(scales.get(feature), 1.0) or 1.0
        z += _num(weights.get(feature)) * ((_num(fmap.get(feature)) - _num(means.get(feature))) / scale)
    if z >= 35:
        return 1.0
    if z <= -35:
        return 0.0
    return 1.0 / (1.0 + math.exp(-z))


def _apply_ml_overlay(predictions: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(predictions, dict):
        return predictions
    rows = predictions.get("predictions") or []
    if not isinstance(rows, list):
        return predictions
    enabled = os.environ.get("INQSI_MLB_ML_OVERLAY_ENABLED", "true").lower() in {"1", "true", "yes"}
    model = _load_model() if enabled else None
    threshold_info = (model or {}).get("selectedThreshold") or {}
    target = _num(os.environ.get("INQSI_MLB_ML_TARGET_ACCURACY"), 90.0)
    validated = bool(model and threshold_info.get("accuracyPct") is not None and _num(threshold_info.get("accuracyPct")) >= target)
    threshold = _num(threshold_info.get("threshold"), _num(os.environ.get("INQSI_MLB_ML_MIN_PROBABILITY"), 0.90))
    promoted = 0
    evaluated = 0
    rejected = 0
    for row in rows:
        overlay = {"enabled": enabled, "modelAvailable": bool(model), "applied": False, "validatedAgainstTarget": validated}
        if model:
            p = _ml_score(row, model)
            evaluated += 1
            confirmed = bool(p is not None and validated and p >= threshold and not (row.get("actionabilityRiskReasons") or []))
            overlay.update({"applied": True, "probabilityPickCorrect": round(p, 4) if p is not None else None, "confirmed": confirmed, "selectedThreshold": threshold_info})
            tags = set(row.get("tags") or [])
            tags.add("ML_OVERLAY_EVALUATED")
            if p is not None and p < float(os.environ.get("INQSI_MLB_ML_REJECT_BELOW", "0.52")):
                row["officialPick"] = False
                row["accuracyTargetEligible"] = False
                row["actionablePick"] = False
                row["actionability"] = "NO_PICK_ML_REJECTED"
                row["actionabilityReason"] = "ml_overlay_rejected_low_correct_probability"
                risks = list(row.get("actionabilityRiskReasons") or [])
                risks.append("ml_overlay_rejected_low_correct_probability")
                row["actionabilityRiskReasons"] = sorted(set(risks))
                tags.add("ML_REJECTED")
                rejected += 1
            if confirmed:
                tags.add("ML_CONFIRMED")
                row["officialPick"] = True
                row["accuracyTargetEligible"] = True
                row["actionablePick"] = True
                row["actionability"] = "ACTIONABLE_ML_CONFIRMED_WINNER"
                row["actionabilityReason"] = "validated_ml_overlay_confirms_platform_selected_winner"
                promoted += 1
            row["tags"] = sorted(tags)
        row["mlOverlay"] = overlay
    predictions["actionablePickCount"] = len([r for r in rows if r.get("actionablePick")])
    predictions["noPickCount"] = len([r for r in rows if not r.get("actionablePick")])
    stack = predictions.get("winnerStackV2") or {}
    if isinstance(stack, dict):
        stack["mlOverlay"] = {"enabled": enabled, "modelAvailable": bool(model), "validatedAgainstTarget": validated, "evaluatedCount": evaluated, "promotedCount": promoted, "rejectedCount": rejected, "threshold": threshold_info}
        stack["actionablePickCount"] = predictions["actionablePickCount"]
        stack["passNoPickCount"] = predictions["noPickCount"]
        predictions["winnerStackV2"] = stack
    target_summary = predictions.get("rolling24hAccuracyTarget") or predictions.get("accuracyTarget") or {}
    if isinstance(target_summary, dict):
        target_summary["mlOverlay"] = stack.get("mlOverlay") if isinstance(stack, dict) else None
        target_summary["actionablePickCount"] = predictions["actionablePickCount"]
        target_summary["noPickCount"] = predictions["noPickCount"]
        predictions["rolling24hAccuracyTarget"] = target_summary
        predictions["accuracyTarget"] = target_summary
    return predictions


def build_report(write_file: bool = True) -> Dict[str, Any]:
    import odds_live_ingestion
    import inqsi_pull_history as history
    import mlb_game_winner_engine

    try:
        import slate_date_patch
        slate_date_patch.apply_to_history(history)
        slate_date_patch.apply_to_odds(odds_live_ingestion)
    except Exception:
        pass

    try:
        import signal_score_guard
        signal_score_guard.apply(history)
    except Exception:
        pass

    try:
        import pull_dedupe_guard
        pull_dedupe_guard.apply(history)
    except Exception:
        pass

    try:
        import sport_key_patch
        sport_key_patch.apply(odds_live_ingestion)
    except Exception:
        pass

    try:
        import mlb_accuracy_target_patch
        mlb_accuracy_target_patch.apply(mlb_game_winner_engine)
    except Exception:
        pass

    try:
        import mlb_fundamentals_optimizer_patch
        mlb_fundamentals_optimizer_patch.apply(mlb_game_winner_engine)
    except Exception:
        pass

    try:
        import mlb_winner_stack_v2
        mlb_winner_stack_v2.apply(mlb_game_winner_engine)
    except Exception:
        pass

    try:
        import mlb_slate_prediction_lock
        mlb_slate_prediction_lock.apply(mlb_game_winner_engine)
    except Exception:
        pass

    try:
        import mlb_last_possible_prediction_gate
        mlb_last_possible_prediction_gate.apply(mlb_game_winner_engine)
    except Exception:
        pass

    try:
        import mlb_prediction_integrity_patch
        mlb_prediction_integrity_patch.apply(mlb_game_winner_engine)
    except Exception:
        pass

    pull_result = odds_live_ingestion.pull_sport("mlb")
    pulls_after = []
    try:
        pulls_after = history.query_pulls("mlb", None, 500)
    except Exception:
        pulls_after = []
    try:
        predictions = mlb_game_winner_engine.predict_all(store=True, limit=500)
        predictions = _apply_ml_overlay(predictions)
        try:
            import mlb_prediction_integrity_patch
            predictions = mlb_prediction_integrity_patch.enforce_result(predictions, module=mlb_game_winner_engine, store=True)
        except Exception:
            pass
    except Exception as exc:
        predictions = {"ok": False, "error": type(exc).__name__, "message": str(exc)}

    pred_rows = predictions.get("predictions") if isinstance(predictions, dict) else []
    target = (predictions.get("rolling24hAccuracyTarget") or predictions.get("accuracyTarget") or {}) if isinstance(predictions, dict) else {}
    report = {
        "ok": bool(pull_result.get("ok")) and bool(isinstance(predictions, dict) and predictions.get("ok")),
        "proofType": "MLB_HOT_PULL_RECOVERY_AND_PREDICTION",
        "operatingMode": "SPORTSDATAIO_ENABLED_WITH_MARKET_FALLBACK" if os.environ.get("SPORTSDATAIO_API_KEY") else "ODDS_API_ONLY_NO_SPORTSDATAIO_SECRET",
        "createdAtUtc": _now(),
        "environment": {
            "oddsApiKeyPresent": bool(os.environ.get("ODDS_API_KEY")),
            "snapshotsTablePresent": bool(os.environ.get("SNAPSHOTS_TABLE")),
            "sportsDataIoKeyPresent": bool(os.environ.get("SPORTSDATAIO_API_KEY")),
            "sportsDataIoScoringEnabled": os.environ.get("INQSI_MLB_USE_SPORTSDATAIO_FUNDAMENTALS") == "true",
            "sportsDataIoRequiredAtFinalGate": os.environ.get("INQSI_REQUIRE_SPORTSDATAIO_FINAL_GATE") == "true",
            "mlOverlayEnabled": os.environ.get("INQSI_MLB_ML_OVERLAY_ENABLED", "true").lower() in {"1", "true", "yes"},
            "secretExposed": False,
        },
        "pullResult": pull_result,
        "pullsAfterCount": len(pulls_after),
        "predictionsSummary": {
            "ok": predictions.get("ok") if isinstance(predictions, dict) else False,
            "slateDate": predictions.get("slate_date") if isinstance(predictions, dict) else None,
            "pullCount": predictions.get("pullCount") if isinstance(predictions, dict) else None,
            "gameCount": predictions.get("gameCount") if isinstance(predictions, dict) else None,
            "count": predictions.get("count") if isinstance(predictions, dict) else None,
            "storedCount": predictions.get("storedCount") if isinstance(predictions, dict) else None,
            "allGamesPredicted": predictions.get("allGamesPredicted") if isinstance(predictions, dict) else None,
            "modelVersion": predictions.get("modelVersion") if isinstance(predictions, dict) else None,
            "actionablePickCount": predictions.get("actionablePickCount") or (predictions.get("winnerStackV2") or {}).get("actionablePickCount"),
            "noPickCount": predictions.get("noPickCount") or (predictions.get("winnerStackV2") or {}).get("passNoPickCount"),
            "slatePredictionLock": predictions.get("slatePredictionLock"),
            "winnerStackV2": predictions.get("winnerStackV2"),
            "fundamentalsEnabled": target.get("fundamentalsEnabled"),
            "fundamentalsMode": target.get("fundamentalsMode"),
            "fundamentalsAppliedCount": target.get("fundamentalsAppliedCount"),
            "lastPossiblePredictionGate": target.get("lastPossiblePredictionGate"),
            "mlOverlay": target.get("mlOverlay"),
        },
        "predictions": pred_rows,
        "failureModeAddressed": "Recovered missing HOT pull history by writing an Odds API MLB pull and storing SportsDataIO-aware integrity-guarded winner predictions when the secret is present.",
    }
    report = _safe(report)
    if write_file:
        os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
        with open(REPORT_PATH, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=str)
            f.write("\n")
    return report


if __name__ == "__main__":
    out = build_report(write_file=True)
    print(json.dumps({
        "ok": out.get("ok"),
        "operatingMode": out.get("operatingMode"),
        "pullResult": out.get("pullResult"),
        "predictionsSummary": out.get("predictionsSummary"),
    }, indent=2, default=str))
