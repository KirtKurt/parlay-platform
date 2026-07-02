from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict

RECOVERY_VERSION = "MLB-HOT-PULL-RECOVERY-LAMBDA-v1.1-odds-line-movement"
SCHEDULE_POLICY = "ODDS_API_LINE_MOVEMENT_TO_MLB_WINNERS_EVERY_15_MIN_START_2026_07_03_1AM_ET"
SCHEDULE_START_UTC = "2026-07-03T05:00:00Z"
SCHEDULE_START_ET = "2026-07-03T01:00:00-04:00"
SCHEDULE_INTERVAL_MINUTES = 15


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


def _store_runtime_report(report: Dict[str, Any]) -> Dict[str, Any]:
    try:
        import inqsi_pull_history as history
        if history.PULLS is None:
            return {"ok": False, "error": "SNAPSHOTS_TABLE not configured"}
        item = history.ddb_safe({
            "PK": "MLB_HOT_PULL_RECOVERY#LATEST",
            "SK": "LATEST",
            "record_type": "mlb_hot_pull_recovery_latest",
            "sport": "mlb",
            "created_at": report.get("createdAtUtc"),
            "data": report,
        })
        run = history.ddb_safe({
            "PK": "MLB_HOT_PULL_RECOVERY#RUNS",
            "SK": f"RUN#{report.get('createdAtUtc')}",
            "record_type": "mlb_hot_pull_recovery_run",
            "sport": "mlb",
            "created_at": report.get("createdAtUtc"),
            "data": report,
        })
        history.PULLS.put_item(Item=item)
        history.PULLS.put_item(Item=run)
        return {"ok": True, "latestPk": item["PK"], "latestSk": item["SK"], "runPk": run["PK"], "runSk": run["SK"]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _apply_runtime_patches(odds_live_ingestion: Any, history: Any, mlb_game_winner_engine: Any) -> Dict[str, Any]:
    applied = []
    try:
        import slate_date_patch
        slate_date_patch.apply_to_history(history)
        slate_date_patch.apply_to_odds(odds_live_ingestion)
        applied.append("slate_date_et")
    except Exception:
        pass

    for module_name, apply_target in [
        ("signal_score_guard", history),
        ("pull_dedupe_guard", history),
        ("sport_key_patch", odds_live_ingestion),
    ]:
        try:
            module = __import__(module_name)
            module.apply(apply_target)
            applied.append(module_name)
        except Exception:
            pass

    for module_name in [
        "mlb_accuracy_target_patch",
        "mlb_fundamentals_optimizer_patch",
        "mlb_last_possible_prediction_gate",
    ]:
        try:
            module = __import__(module_name)
            module.apply(mlb_game_winner_engine)
            applied.append(module_name)
        except Exception:
            pass
    return {"applied": sorted(set(applied))}


def build_report(event: Dict[str, Any] | None = None, store_report: bool = True) -> Dict[str, Any]:
    event = event or {}
    import odds_live_ingestion
    import inqsi_pull_history as history
    import mlb_game_winner_engine

    patch_report = _apply_runtime_patches(odds_live_ingestion, history, mlb_game_winner_engine)

    try:
        pull_result = odds_live_ingestion.pull_sport("mlb")
    except Exception as exc:
        pull_result = {"ok": False, "error": type(exc).__name__, "message": str(exc)}

    try:
        pulls_after = history.query_pulls("mlb", None, 500)
    except Exception:
        pulls_after = []

    try:
        predictions = mlb_game_winner_engine.predict_all(store=True, limit=500)
    except Exception as exc:
        predictions = {"ok": False, "error": type(exc).__name__, "message": str(exc)}

    target = (predictions.get("rolling24hAccuracyTarget") or predictions.get("accuracyTarget") or {}) if isinstance(predictions, dict) else {}
    pull_ok = bool(pull_result.get("ok"))
    prediction_ok = bool(isinstance(predictions, dict) and predictions.get("ok"))
    predictions_list = predictions.get("predictions") if isinstance(predictions, dict) else []
    all_games_predicted = bool(predictions.get("allGamesPredicted")) if isinstance(predictions, dict) else False

    report = {
        "ok": pull_ok and prediction_ok,
        "proofType": "MLB_ODDS_API_LINE_MOVEMENT_PULL_STORE_PREDICT",
        "recoveryVersion": RECOVERY_VERSION,
        "createdAtUtc": _now(),
        "trigger": event.get("run") or event.get("source") or "aws_eventbridge_scheduler_dedicated_mlb_line_movement",
        "cadencePolicy": {
            "policy": SCHEDULE_POLICY,
            "intervalMinutes": SCHEDULE_INTERVAL_MINUTES,
            "startAtEt": SCHEDULE_START_ET,
            "startAtUtc": SCHEDULE_START_UTC,
            "scheduler": "AWS::Scheduler::Schedule",
            "scheduleExpression": "cron(0/15 * * * ? *)",
            "scheduleExpressionTimezone": "America/New_York",
        },
        "lineMovementDataFlow": {
            "source": "the_odds_api",
            "sportKey": "baseball_mlb",
            "markets": "h2h,spreads,totals",
            "storagePath": "SNAPSHOTS_TABLE/PULLS#mlb#YYYY-MM-DD",
            "winnerEngine": "mlb_game_winner_engine.predict_all",
            "features": [
                "de-vigged moneyline consensus probability",
                "line-movement delta across stored pulls",
                "book count and book divergence",
                "reversals",
                "run-line movement and confirmation",
                "pull depth",
            ],
        },
        "environment": {
            "oddsApiKeyPresent": bool(odds_live_ingestion.ODDS_API_KEY),
            "snapshotsTablePresent": bool(getattr(history, "SNAPSHOTS_TABLE", "")),
            "secretExposed": False,
        },
        "runtimePatches": patch_report,
        "pullResult": pull_result,
        "pullsAfterCount": len(pulls_after),
        "latestStoredPullAt": pulls_after[-1].get("pulled_at") if pulls_after else None,
        "predictionsSummary": {
            "ok": prediction_ok,
            "slateDate": predictions.get("slate_date") if isinstance(predictions, dict) else None,
            "pullCount": predictions.get("pullCount") if isinstance(predictions, dict) else None,
            "gameCount": predictions.get("gameCount") if isinstance(predictions, dict) else None,
            "count": predictions.get("count") if isinstance(predictions, dict) else None,
            "storedCount": predictions.get("storedCount") if isinstance(predictions, dict) else None,
            "allGamesPredicted": all_games_predicted,
            "modelVersion": predictions.get("modelVersion") if isinstance(predictions, dict) else None,
            "fundamentalsEnabled": target.get("fundamentalsEnabled"),
            "fundamentalsAppliedCount": target.get("fundamentalsAppliedCount"),
            "lastPossiblePredictionGate": target.get("lastPossiblePredictionGate"),
        },
        "predictions": predictions_list or [],
        "permanentFix": "Dedicated EventBridge Scheduler invokes the MLB recovery Lambda every 15 minutes from the 2026-07-03 1:00 AM ET boundary; each run pulls Odds API MLB lines, stores the pull history, and writes game-winner predictions.",
    }
    report = _safe(report)
    if store_report:
        report["storedRuntimeReport"] = _store_runtime_report(report)
    return report


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    report = build_report(event or {}, store_report=True)
    return {"statusCode": 200 if report.get("ok") else 500, "body": json.dumps(report, default=str)}
