from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import boto3

import inqsi_pull_history as history
import mlb_daily_pick_lock
import mlb_game_winner_engine

EASTERN = ZoneInfo("America/New_York")
SNAPSHOTS_TABLE = os.environ.get("SNAPSHOTS_TABLE", "")
MAX_PULL_AGE_MINUTES = float(os.environ.get("MLB_VERIFY_MAX_PULL_AGE_MINUTES", "20"))
DDB = boto3.resource("dynamodb")
TABLE = DDB.Table(SNAPSHOTS_TABLE) if SNAPSHOTS_TABLE else None


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    return str(value)


def _resp(status: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body, default=_json_default),
    }


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _today_et() -> str:
    return _now_utc().astimezone(EASTERN).date().isoformat()


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _age_minutes(value: Any) -> Optional[float]:
    parsed = _parse_dt(value)
    if not parsed:
        return None
    return round(max((_now_utc() - parsed).total_seconds(), 0.0) / 60.0, 2)


def _payload(event: Dict[str, Any]) -> Dict[str, Any]:
    event = event or {}
    out: Dict[str, Any] = {}
    if not event.get("httpMethod") and not event.get("requestContext"):
        out.update(event)
    query = event.get("queryStringParameters") or {}
    if isinstance(query, dict):
        out.update(query)
    body = event.get("body")
    if body:
        try:
            parsed = json.loads(body)
            if isinstance(parsed, dict):
                out.update(parsed)
        except Exception:
            pass
    return out


def _verification_payload(slate_date: str, mode: str, run: str) -> Dict[str, Any]:
    pulls = history.query_pulls("mlb", slate_date, 500)
    latest_pull = pulls[-1] if pulls else {}
    latest_age = _age_minutes(latest_pull.get("pulled_at")) if latest_pull else None
    predictions = mlb_game_winner_engine.predict_all(slate_date, store=False, limit=500)
    lock_status = mlb_daily_pick_lock._status_payload(slate_date)  # internal status read; no writes and no Odds API call

    blockers: List[str] = []
    game_count = int(predictions.get("gameCount") or 0)
    prediction_count = int(predictions.get("count") or 0)
    pull_count = len(pulls)

    if mode in {"continuous", "ingest"}:
        if pull_count == 0:
            blockers.append("NO_STORED_MLB_PULLS")
        if latest_age is None:
            blockers.append("LATEST_PULL_AGE_UNKNOWN")
        elif latest_age > MAX_PULL_AGE_MINUTES:
            blockers.append("LATEST_PULL_STALE")
        if game_count > 0 and prediction_count == 0:
            blockers.append("NO_SINGLE_GAME_PREDICTIONS")
        if game_count > 0 and prediction_count < game_count:
            blockers.append("INCOMPLETE_SINGLE_GAME_CARD")

    if mode == "lock":
        if bool(lock_status.get("lockDue")) and game_count > 0 and not bool(lock_status.get("locked")):
            blockers.append("LOCK_DUE_BUT_NOT_LOCKED")

    ok = not blockers
    return {
        "ok": ok,
        "sport": "mlb",
        "mode": mode,
        "run": run,
        "slateDateEt": slate_date,
        "checkedAt": _now_utc().isoformat(),
        "pullCount": pull_count,
        "latestPullAt": latest_pull.get("pulled_at"),
        "latestPullAgeMinutes": latest_age,
        "latestPullFresh": bool(latest_age is not None and latest_age <= MAX_PULL_AGE_MINUTES),
        "gameCount": game_count,
        "predictionCount": prediction_count,
        "promotedCount": predictions.get("promotedCount"),
        "allGamesPredicted": predictions.get("allGamesPredicted"),
        "lock": {
            "locked": lock_status.get("locked"),
            "lockDue": lock_status.get("lockDue"),
            "lockTimeEt": lock_status.get("lockTimeEt"),
            "minutesUntilLock": lock_status.get("minutesUntilLock"),
        },
        "blockers": blockers,
    }


def _store(result: Dict[str, Any]) -> None:
    if TABLE is None:
        return
    slate_date = str(result.get("slateDateEt") or _today_et())
    checked = str(result.get("checkedAt") or _now_utc().isoformat())
    item = history.ddb_safe({
        "PK": f"VERIFY#mlb#{slate_date}",
        "SK": f"VERIFY#{checked}#{result.get('mode') or 'continuous'}",
        "record_type": "mlb_production_verification",
        "sport": "mlb",
        "slate_date": slate_date,
        "ok": result.get("ok"),
        "mode": result.get("mode"),
        "run": result.get("run"),
        "created_at": checked,
        "data": result,
    })
    TABLE.put_item(Item=item)


def lambda_handler(event, context):
    try:
        payload = _payload(event or {})
        slate_date = payload.get("date") or payload.get("slate_date") or payload.get("slateDateEt") or _today_et()
        mode = str(payload.get("mode") or "continuous")
        run = str(payload.get("run") or f"mlb_production_verify_{mode}")
        result = _verification_payload(str(slate_date), mode, run)
        _store(result)
        return _resp(200 if result.get("ok") else 207, result)
    except Exception as exc:
        result = {
            "ok": False,
            "sport": "mlb",
            "checkedAt": _now_utc().isoformat(),
            "error": str(exc),
            "blockers": ["VERIFIER_EXCEPTION"],
        }
        _store(result)
        return _resp(500, result)
