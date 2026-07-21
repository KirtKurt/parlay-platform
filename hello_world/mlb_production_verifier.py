from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import boto3
from boto3.dynamodb.conditions import Key

import inqsi_pull_history as history
import mlb_daily_pick_lock
import mlb_game_winner_engine
import mlb_ml_clean_cohort_v1

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


def _row_game_id(row: Dict[str, Any]) -> str:
    return str(
        row.get("id")
        or row.get("gameId")
        or row.get("game_id")
        or row.get("providerGameId")
        or row.get("provider_game_id")
        or (row.get("lockedCardAudit") or {}).get("providerGameId")
        or ""
    )


def _row_lock_at(row: Dict[str, Any]) -> Optional[datetime]:
    audit = row.get("lockedCardAudit") or {}
    for value in (
        audit.get("lockAtUtc"),
        (row.get("slatePredictionLock") or {}).get("lockAtUtc"),
        (row.get("lastPossiblePredictionGate") or {}).get("lockAtUtc"),
        row.get("lockedAtUtc"),
    ):
        parsed = _parse_dt(value)
        if parsed:
            return parsed
    return None


def _is_locked_prediction(row: Dict[str, Any]) -> bool:
    tags = {str(value) for value in (row.get("tags") or [])}
    audit = row.get("lockedCardAudit") or {}
    return bool(
        row.get("lockedPrediction") is True
        or row.get("officialPredictionStatus") == "OFFICIAL_LOCKED_PREDICTION"
        or row.get("officialPrediction") is True
        or audit.get("lockedFlag") is True
        or "SLATE_LOCKED" in tags
        or "FINAL_LOCKED" in tags
        or "OFFICIAL_LOCKED_PREDICTION" in tags
    )


def _expected_fingerprint(vector: Dict[str, Any]) -> str:
    source = json.dumps(
        {
            "gameId": vector.get("gameId"),
            "lockAtUtc": vector.get("lockAtUtc"),
            "features": vector.get("features") or {},
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _query_stored_predictions(slate_date: str) -> List[Dict[str, Any]]:
    if TABLE is None:
        return []
    rows: List[Dict[str, Any]] = []
    start_key = None
    while True:
        args: Dict[str, Any] = {
            "KeyConditionExpression": Key("PK").eq(f"GAME_WINNERS#mlb#{slate_date}"),
            "ConsistentRead": True,
        }
        if start_key:
            args["ExclusiveStartKey"] = start_key
        response = TABLE.query(**args)
        for item in response.get("Items") or []:
            data = item.get("data") if isinstance(item.get("data"), dict) else item
            if isinstance(data, dict):
                rows.append(data)
        start_key = response.get("LastEvaluatedKey")
        if not start_key:
            return rows


def _vector_validation(row: Dict[str, Any]) -> Dict[str, Any]:
    vector = row.get("frozenFeatureVector") or {}
    game_id = _row_game_id(row)
    row_lock = _row_lock_at(row)
    vector_lock = _parse_dt(vector.get("lockAtUtc"))
    source_at = _parse_dt(vector.get("sourcePullAtUtc"))
    fingerprint = str(vector.get("fingerprint") or "")
    reasons: List[str] = []
    if not _is_locked_prediction(row):
        reasons.append("not_locked_prediction")
    if not game_id:
        reasons.append("missing_game_id")
    if not isinstance(vector, dict) or not vector:
        reasons.append("missing_frozen_feature_vector")
    if str(vector.get("version") or "") != mlb_ml_clean_cohort_v1.FEATURE_SNAPSHOT_VERSION:
        reasons.append("wrong_frozen_feature_vector_version")
    if str(vector.get("gameId") or "") != game_id:
        reasons.append("frozen_vector_game_identity_mismatch")
    if not isinstance(vector.get("features"), dict) or not vector.get("features"):
        reasons.append("frozen_vector_features_missing")
    if not fingerprint:
        reasons.append("missing_fingerprint")
    elif fingerprint != _expected_fingerprint(vector):
        reasons.append("fingerprint_mismatch")
    if not row_lock or not vector_lock or row_lock != vector_lock:
        reasons.append("lock_timestamp_mismatch")
    if not source_at or not vector_lock or source_at > vector_lock:
        reasons.append("source_after_or_missing_lock")
    return {
        "ok": not reasons,
        "gameId": game_id,
        "fingerprint": fingerprint or None,
        "version": vector.get("version"),
        "rowLockAtUtc": row_lock.isoformat() if row_lock else None,
        "vectorLockAtUtc": vector_lock.isoformat() if vector_lock else None,
        "sourcePullAtUtc": source_at.isoformat() if source_at else None,
        "reasons": sorted(set(reasons)),
    }


def _locked_row_integrity(
    slate_date: str, expected_count: int, evaluate_full_slate: bool
) -> Dict[str, Any]:
    stored = _query_stored_predictions(slate_date)
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in stored:
        game_id = _row_game_id(row)
        if game_id:
            grouped.setdefault(game_id, []).append(row)

    selected: List[Dict[str, Any]] = []
    for candidates in grouped.values():
        evaluated = [(row, _vector_validation(row)) for row in candidates]
        valid = [pair for pair in evaluated if pair[1].get("ok")]
        if valid:
            selected.append(max(valid, key=lambda pair: str(pair[0].get("createdAt") or pair[0].get("created_at") or ""))[0])
        else:
            selected.append(max(evaluated, key=lambda pair: str(pair[0].get("createdAt") or pair[0].get("created_at") or ""))[0])

    checks = [_vector_validation(row) for row in selected]
    valid = [check for check in checks if check.get("ok")]
    invalid = [check for check in checks if not check.get("ok")]
    complete = bool(
        evaluate_full_slate
        and expected_count > 0
        and len(selected) == expected_count
        and len(valid) == expected_count
    )
    return {
        "evaluated": bool(evaluate_full_slate),
        "requiredFeatureVectorVersion": mlb_ml_clean_cohort_v1.FEATURE_SNAPSHOT_VERSION,
        "expectedGameCount": expected_count,
        "rawStoredRowCount": len(stored),
        "deduplicatedStoredGameCount": len(selected),
        "validFingerprintCount": len(valid),
        "invalidFingerprintCount": len(invalid),
        "coverageComplete": complete,
        "invalidRows": invalid,
        "checks": checks,
        "policy": "Every locked manifest game must have one stored immutable vector whose fingerprint, game identity, lock timestamp, and pre-lock source timestamp recompute successfully.",
    }


def _per_game_lock_progress(
    lock_status: Dict[str, Any], *, checked_at: datetime, expected_count: int
) -> Dict[str, Any]:
    raw_statuses = lock_status.get("perGameStatus") or []
    statuses = [row for row in raw_statuses if isinstance(row, dict)]
    due: List[Dict[str, Any]] = []
    pending: List[Dict[str, Any]] = []
    invalid: List[Dict[str, Any]] = []

    for row in statuses:
        game_id = str(row.get("gameIdentity") or row.get("gameId") or "")
        lock_at = _parse_dt(row.get("scheduledLockAtUtc"))
        compact = {
            "gameId": game_id or None,
            "scheduledLockAtUtc": lock_at.isoformat() if lock_at else None,
            "lockStatus": row.get("lockStatus") or row.get("state"),
            "lockOutcomeRecorded": row.get("lockOutcomeRecorded") is True,
            "lockedPrediction": row.get("lockedPrediction") is True,
        }
        if not lock_at:
            invalid.append(compact)
        elif lock_at <= checked_at:
            due.append(compact)
        else:
            pending.append(compact)

    missing_due = [row for row in due if row["lockOutcomeRecorded"] is not True]
    valid_cutoffs = [
        _parse_dt(row.get("scheduledLockAtUtc"))
        for row in statuses
        if _parse_dt(row.get("scheduledLockAtUtc"))
    ]
    final_cutoff = max(valid_cutoffs) if valid_cutoffs else None
    status_complete = bool(
        expected_count > 0
        and len(statuses) == expected_count
        and len(valid_cutoffs) == expected_count
    )
    final_cutoff_reached = bool(
        status_complete and final_cutoff and checked_at >= final_cutoff
    )
    terminal_slate = bool(
        lock_status.get("lockStatusComplete") is True
        or lock_status.get("dailyCardComplete") is True
    )
    return {
        "statusComplete": status_complete,
        "statusCount": len(statuses),
        "invalidStatusCount": len(invalid),
        "invalidStatuses": invalid,
        "dueGameCount": len(due),
        "dueTerminalGameCount": len(due) - len(missing_due),
        "dueMissingGameCount": len(missing_due),
        "dueMissingGames": missing_due,
        "pendingGameCount": len(pending),
        "finalPerGameCutoffAtUtc": (
            final_cutoff.isoformat() if final_cutoff else None
        ),
        "finalPerGameCutoffReached": final_cutoff_reached,
        "terminalSlate": terminal_slate,
        "fullSlateVectorEvaluationDue": bool(
            terminal_slate or final_cutoff_reached
        ),
        "policy": (
            "Only games whose own scheduled T-45 cutoff is at or before "
            "checkedAt must have a terminal lock outcome. Full-slate vector "
            "coverage is evaluated only after the final per-game cutoff or "
            "an explicitly terminal slate."
        ),
    }


def _verification_payload(slate_date: str, mode: str, run: str) -> Dict[str, Any]:
    checked_at = _now_utc()
    pulls = history.query_pulls("mlb", slate_date, 500)
    latest_pull = pulls[-1] if pulls else {}
    latest_age = _age_minutes(latest_pull.get("pulled_at")) if latest_pull else None
    predictions = mlb_game_winner_engine.predict_all(slate_date, store=False, limit=500)
    lock_status = mlb_daily_pick_lock._status_payload(slate_date)

    blockers: List[str] = []
    game_count = int(predictions.get("gameCount") or 0)
    prediction_count = int(predictions.get("count") or 0)
    pull_count = len(pulls)
    lock_data = lock_status.get("lock") or {}
    expected_slate_count = int(
        lock_status.get("gameCount")
        or lock_data.get("gameCount")
        or lock_data.get("predictionCount")
        or game_count
        or 0
    )
    per_game = _per_game_lock_progress(
        lock_status,
        checked_at=checked_at,
        expected_count=expected_slate_count,
    )
    integrity = _locked_row_integrity(
        slate_date,
        expected_slate_count,
        per_game["fullSlateVectorEvaluationDue"],
    )

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

    if mode in {"continuous", "lock"}:
        if expected_slate_count > 0 and per_game["statusComplete"] is not True:
            blockers.append("PER_GAME_LOCK_STATUS_MISSING_OR_INVALID")
        if per_game["dueMissingGameCount"] > 0:
            blockers.append("LOCK_DUE_BUT_NOT_LOCKED")
        if (
            per_game["fullSlateVectorEvaluationDue"] is True
            and not integrity.get("coverageComplete")
        ):
            blockers.append("LOCKED_ROWS_MISSING_VALID_FROZEN_FINGERPRINTS")

    ok = not blockers
    return {
        "ok": ok,
        "sport": "mlb",
        "mode": mode,
        "run": run,
        "slateDateEt": slate_date,
        "checkedAt": checked_at.isoformat(),
        "pullCount": pull_count,
        "latestPullAt": latest_pull.get("pulled_at"),
        "latestPullAgeMinutes": latest_age,
        "latestPullFresh": bool(latest_age is not None and latest_age <= MAX_PULL_AGE_MINUTES),
        "gameCount": game_count,
        "predictionCount": prediction_count,
        "promotedCount": predictions.get("promotedCount"),
        "allGamesPredicted": predictions.get("allGamesPredicted"),
        "lock": {
            "locked": bool(lock_status.get("locked")),
            "lockDue": lock_status.get("lockDue"),
            "lockTimeEt": lock_status.get("lockTimeEt"),
            "minutesUntilLock": lock_status.get("minutesUntilLock"),
            "expectedSlateGameCount": expected_slate_count,
            "expectedLockedGameCount": per_game["dueGameCount"],
            "perGameProgress": per_game,
        },
        "lockedRowIntegrity": integrity,
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
