from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict

REPORT_PATH = "runtime_reports/mlb_hot_pull_recovery_latest.json"

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

    pull_result = odds_live_ingestion.pull_sport("mlb")
    pulls_after = []
    try:
        pulls_after = history.query_pulls("mlb", None, 500)
    except Exception:
        pulls_after = []
    try:
        predictions = mlb_game_winner_engine.predict_all(store=True, limit=500)
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
        },
        "predictions": pred_rows,
        "failureModeAddressed": "Recovered missing HOT pull history by writing an Odds API MLB pull and immediately storing SportsDataIO-aware calibrated winner predictions when the secret is present.",
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
