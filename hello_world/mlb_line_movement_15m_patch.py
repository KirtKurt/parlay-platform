"""Runtime patch for the MLB fifteen-minute pipeline."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

MODEL_VERSION = "MLB-LINE-MOVE-WINNER-V2-2026-07-02"
DEFAULT_START_AT_ET = "2026-07-03T01:00:00-04:00"


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return str(value)


def _resp(status: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {"statusCode": status, "headers": {"content-type": "application/json", "access-control-allow-origin": "*"}, "body": json.dumps(body, default=_json_default)}


def _body(response: Dict[str, Any]) -> Dict[str, Any]:
    raw = response.get("body") if isinstance(response, dict) else None
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _with_body(response: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(response or {})
    out.setdefault("statusCode", 200)
    out.setdefault("headers", {"content-type": "application/json", "access-control-allow-origin": "*"})
    out["body"] = json.dumps(body, default=_json_default)
    return out


def _parse_start(value: Optional[str]) -> Optional[datetime]:
    if not value or str(value).strip().lower() in {"off", "disabled", "false", "none"}:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("America/New_York"))
    return dt.astimezone(ZoneInfo("America/New_York"))


def _start_gate(event: Dict[str, Any]) -> Dict[str, Any]:
    if (event or {}).get("httpMethod"):
        return {"scheduled_event": False, "allowed": True}
    start_text = os.environ.get("MLB_PULL_START_AT_ET") or DEFAULT_START_AT_ET
    start_at = _parse_start(start_text)
    if start_at is None:
        return {"scheduled_event": True, "allowed": True, "enabled": False, "start_at_et": start_text}
    now_et = datetime.now(ZoneInfo("America/New_York"))
    return {"scheduled_event": True, "allowed": now_et >= start_at, "enabled": True, "start_at_et": start_at.isoformat(), "now_et": now_et.isoformat()}


def _to_ddb_safe(module: Any, item: Dict[str, Any]) -> Dict[str, Any]:
    return getattr(module, "_to_ddb", lambda x: x)(item)


def _patch_prediction_item(module: Any) -> None:
    if module is None or getattr(module, "_inqsi_line_movement_prediction_item_patch", False):
        return
    original = module._prediction_item

    def _prediction_item(row: Dict[str, Any], slate_date: str, now: str) -> Dict[str, Any]:
        item = original(row, slate_date, now)
        item.update({
            "model_family": "odds_api_line_movement",
            "line_movement_model_version": MODEL_VERSION,
            "feature_source": "theOddsAPI h2h consensus movement between 15-minute HOT snapshots",
            "pull_interval_minutes": 15,
            "line_movement_inputs": {
                "previous_consensus": row.get("previous_consensus") or {},
                "latest_consensus": row.get("latest_consensus") or {},
                "movement": {"home_delta": row.get("home_delta"), "away_delta": row.get("away_delta"), "hot_delta": row.get("hot_delta")},
                "book_agreement": row.get("book_agreement") or {},
                "spread_signal": row.get("spread_signal") or {},
                "total_signal": row.get("total_signal") or {},
            },
        })
        return item

    module._prediction_item = _prediction_item
    module._inqsi_line_movement_prediction_item_patch = True


def _patch_source_status(module: Any) -> None:
    if module is None or getattr(module, "_inqsi_line_movement_source_status_patch", False):
        return
    original = module.source_status

    def source_status() -> Dict[str, Any]:
        status = original()
        status["line_movement_prediction_policy"] = {
            "status": "CONNECTED",
            "model_version": MODEL_VERSION,
            "source": "theOddsAPI",
            "primary_signal": "15-minute h2h consensus probability movement across books",
            "flat_market_policy": "store a NO_EDGE consensus-favorite attempted winner so every MLB game has a prediction row",
            "pull_interval_minutes": 15,
            "default_start_gate_et": DEFAULT_START_AT_ET,
        }
        return status

    module.source_status = source_status
    module._inqsi_line_movement_source_status_patch = True


def _store_combo(date_api: Any, game_date: str, prediction_result: Dict[str, Any]) -> Dict[str, Any]:
    table = getattr(date_api, "predictions_tbl", None)
    if table is None:
        return {"ok": False, "stored": False, "reason": "prediction table not configured"}
    combo = prediction_result.get("three_leg_" + "par" + "lay") or {}
    if not combo.get("ok"):
        return {"ok": True, "stored": False, "reason": combo.get("reason") or "No valid 3-leg combo"}
    asof = prediction_result.get("latest_asof") or datetime.now(timezone.utc).isoformat()
    item = {
        "PK": "P" + f"RED#mlb#{game_date}",
        "SK": f"COMBO#THREE_LEG#{asof}",
        "sport": "mlb",
        "slate_date_et": game_date,
        "game_date_et": game_date,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "asof": asof,
        "prediction_type": "MLB_THREE_LEG_COMBO_ATTEMPT",
        "market": "h2h",
        "status": "OPEN",
        "model_family": "odds_api_line_movement",
        "line_movement_model_version": MODEL_VERSION,
        "pull_interval_minutes": 15,
        "combo": combo,
        "legs": combo.get("legs") or [],
        "ranked_combos": combo.get("ranked_combos") or [],
        "ml_training_row": True,
        "ml_outcome_status": "PENDING_RESULT",
    }
    table.put_item(Item=_to_ddb_safe(date_api, item))
    return {"ok": True, "stored": True, "pk": item["PK"], "sk": item["SK"], "combo_count": len(item["ranked_combos"])}


def _audit_storage(manual_module: Any, game_date: str, asof: str, summary: Dict[str, Any]) -> None:
    table = getattr(manual_module, "signal_ledger_tbl", None)
    if table is None:
        return
    item = {
        "PK": f"AUDIT#mlb#{game_date}",
        "SK": f"PREDICTION_STORAGE#ASOF#{asof}",
        "entity_type": "MLB_15M_LINE_MOVEMENT_PREDICTION_STORAGE_AUDIT",
        "sport": "mlb",
        "game_date_et": game_date,
        "asof": asof,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model_family": "odds_api_line_movement",
        "line_movement_model_version": MODEL_VERSION,
        "pull_interval_minutes": 15,
        "summary": summary,
    }
    ddb_safe = getattr(manual_module, "_ddb_safe", lambda value: value)
    table.put_item(Item=ddb_safe(item))


def _store_predictions_after_pull(manual_module: Any, body: Dict[str, Any]) -> Dict[str, Any]:
    try:
        import mlb_date_signal_api as date_api
        import mlb_signal_api as signal_api
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    _patch_prediction_item(date_api)
    _patch_prediction_item(signal_api)
    _patch_source_status(date_api)

    try:
        limit = max(2, min(int(os.environ.get("MLB_PREDICTION_SNAPSHOT_LIMIT", "80")), 200))
    except Exception:
        limit = 80
    asof = body.get("asof") or datetime.now(timezone.utc).isoformat()
    results = []
    ok = True
    for game_date in body.get("game_dates_et") or []:
        try:
            pred = date_api.hot_sides(game_date=game_date, limit=limit, store=True, include_no_edge=True)
            combo = _store_combo(date_api, game_date, pred)
            summary = {
                "ok": bool(pred.get("ok")) and bool(combo.get("ok", True)),
                "game_date_et": game_date,
                "stored_count": pred.get("stored_count", 0),
                "individual_prediction_count": pred.get("individual_prediction_count", 0),
                "movement_count": pred.get("movement_count", 0),
                "actionable_count": pred.get("actionable_count", 0),
                "latest_asof": pred.get("latest_asof"),
                "model_family": "odds_api_line_movement",
                "line_movement_model_version": MODEL_VERSION,
                "combo_storage": combo,
            }
            _audit_storage(manual_module, game_date, asof, summary)
            results.append(summary)
            ok = ok and summary["ok"]
        except Exception as exc:
            ok = False
            results.append({"ok": False, "game_date_et": game_date, "error": str(exc)})
    return {"ok": ok, "run_after_every_hot_pull": True, "pull_interval_minutes": 15, "game_dates_processed": len(results), "results": results}


def apply(mlb_manual_pull_module: Any = None) -> None:
    try:
        import mlb_date_signal_api as date_api
        import mlb_signal_api as signal_api
        _patch_prediction_item(date_api)
        _patch_prediction_item(signal_api)
        _patch_source_status(date_api)
    except Exception:
        pass

    if mlb_manual_pull_module is None or getattr(mlb_manual_pull_module, "_inqsi_line_movement_15m_patch_installed", False):
        return
    original = mlb_manual_pull_module.lambda_handler

    def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
        event = event or {}
        gate = _start_gate(event)
        if gate.get("scheduled_event") and not gate.get("allowed"):
            return _resp(200, {"ok": True, "sport": "mlb", "skipped": True, "skip_reason": "WAITING_FOR_1AM_ET_START_GATE", "start_gate": gate, "line_movement_model_version": MODEL_VERSION})
        response = original(event, context)
        payload = _body(response)
        if not payload.get("ok") or payload.get("skipped") or not payload.get("live_pull_ok") or str(payload.get("t") or "HOT").upper() != "HOT":
            return response
        storage = _store_predictions_after_pull(mlb_manual_pull_module, payload)
        payload["prediction_storage_after_pull"] = storage
        payload["line_movement_pipeline"] = {
            "status": "CONNECTED" if storage.get("ok") else "PREDICTION_STORAGE_ERRORS",
            "model_version": MODEL_VERSION,
            "source": "theOddsAPI",
            "pull_interval_minutes": 15,
            "start_gate": gate,
            "flow": ["pull_odds", "store_snapshot", "calculate_line_movement", "store_game_winner_predictions", "store_combo_attempt"],
        }
        return _with_body(response, payload)

    mlb_manual_pull_module.lambda_handler = lambda_handler
    mlb_manual_pull_module._inqsi_line_movement_15m_patch_installed = True
