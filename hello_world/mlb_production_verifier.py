from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional
from zoneinfo import ZoneInfo

import boto3
from boto3.dynamodb.conditions import Key

import inqsi_pull_history as history
import mlb_daily_pick_lock
import mlb_game_winner_engine
import mlb_ml_clean_cohort_v1
import mlb_slate_coverage_patch

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


def _identity_from_value(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    normalized = mlb_slate_coverage_patch.game_identity({"gameId": text})
    return normalized[len("provider:") :] if normalized.startswith("provider:") else normalized


def _row_identity(row: Any) -> str:
    if not isinstance(row, dict):
        return ""
    for key in (
        "gameIdentity",
        "gameId",
        "game_id",
        "id",
        "providerGameId",
        "provider_game_id",
    ):
        if row.get(key) not in (None, ""):
            return _identity_from_value(row.get(key))
    if (
        (row.get("homeTeam") or row.get("home_team"))
        and (row.get("awayTeam") or row.get("away_team"))
        and (row.get("commenceTime") or row.get("commence_time"))
    ):
        normalized = mlb_slate_coverage_patch.game_identity(row)
        return (
            normalized[len("provider:") :]
            if normalized.startswith("provider:")
            else normalized
        )
    return ""


def _identity_summary(rows: Any) -> Dict[str, Any]:
    source = rows if isinstance(rows, list) else []
    identities = [_row_identity(row) for row in source]
    valid = [identity for identity in identities if identity]
    counts: Dict[str, int] = {}
    for identity in valid:
        counts[identity] = counts.get(identity, 0) + 1
    duplicates = sorted(
        identity for identity, count in counts.items() if count > 1
    )
    return {
        "rawCount": len(source),
        "validIdentityCount": len(valid),
        "uniqueCount": len(counts),
        "gameIdentities": sorted(counts),
        "duplicateGameIdentities": duplicates,
        "missingIdentityRowCount": len(source) - len(valid),
    }


def _exact_count(value: Any, expected: int) -> bool:
    if isinstance(value, bool):
        return False
    try:
        return int(value) == expected and float(value) == float(expected)
    except (TypeError, ValueError, OverflowError):
        return False


def _count_or_zero(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(int(value), 0)
    except (TypeError, ValueError, OverflowError):
        return 0


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


def _query_stored_predictions(slate_date: str) -> List[Dict[str, Any]]:
    if TABLE is None:
        return []
    rows: List[Dict[str, Any]] = []
    start_key = None
    while True:
        args: Dict[str, Any] = {
            "KeyConditionExpression": (
                Key("PK").eq(f"GAME_WINNERS#mlb#{slate_date}")
                & Key("SK").begins_with("LOCKED#GAME#")
            ),
            "ConsistentRead": True,
        }
        if start_key:
            args["ExclusiveStartKey"] = start_key
        response = TABLE.query(**args)
        for item in response.get("Items") or []:
            if not isinstance(item, dict) or not str(item.get("SK") or "").startswith(
                "LOCKED#GAME#"
            ):
                continue
            data = item.get("data") if isinstance(item.get("data"), dict) else None
            if data is not None:
                rows.append(data)
        start_key = response.get("LastEvaluatedKey")
        if not start_key:
            return rows


def _vector_validation(row: Dict[str, Any]) -> Dict[str, Any]:
    vector = row.get("frozenFeatureVector") or {}
    if not isinstance(vector, dict):
        vector = {}
    game_id = _row_game_id(row)
    row_lock = _row_lock_at(row)
    vector_lock = _parse_dt(vector.get("lockAtUtc"))
    source_at = _parse_dt(vector.get("sourcePullAtUtc"))
    fingerprint = str(vector.get("fingerprint") or "")
    fingerprint_version = str(vector.get("fingerprintVersion") or "")
    canonical_fingerprint = mlb_ml_clean_cohort_v1.fingerprint_for_vector(vector)
    reasons: List[str] = []
    if not _is_locked_prediction(row):
        reasons.append("not_locked_prediction")
    if not game_id:
        reasons.append("missing_game_id")
    if not isinstance(vector, dict) or not vector:
        reasons.append("missing_frozen_feature_vector")
    if str(vector.get("version") or "") != mlb_ml_clean_cohort_v1.FEATURE_SNAPSHOT_VERSION:
        reasons.append("wrong_frozen_feature_vector_version")
    if _identity_from_value(vector.get("gameId")) != _identity_from_value(game_id):
        reasons.append("frozen_vector_game_identity_mismatch")
    if not isinstance(vector.get("features"), dict) or not vector.get("features"):
        reasons.append("frozen_vector_features_missing")
    if fingerprint_version != mlb_ml_clean_cohort_v1.FINGERPRINT_VERSION:
        reasons.append(
            "missing_fingerprint_version"
            if not fingerprint_version
            else "unsupported_fingerprint_version"
        )
    if not fingerprint:
        reasons.append("missing_fingerprint")
    elif not canonical_fingerprint or fingerprint != canonical_fingerprint:
        reasons.append("fingerprint_mismatch")
    if not row_lock or not vector_lock or row_lock != vector_lock:
        reasons.append("lock_timestamp_mismatch")
    if not source_at or not vector_lock or source_at > vector_lock:
        reasons.append("source_after_or_missing_lock")
    return {
        "ok": not reasons,
        "gameId": game_id,
        "fingerprint": fingerprint or None,
        "fingerprintVersion": fingerprint_version or None,
        "requiredFingerprintVersion": mlb_ml_clean_cohort_v1.FINGERPRINT_VERSION,
        "canonicalFingerprintMatches": bool(
            fingerprint and canonical_fingerprint and fingerprint == canonical_fingerprint
        ),
        "version": vector.get("version"),
        "rowLockAtUtc": row_lock.isoformat() if row_lock else None,
        "vectorLockAtUtc": vector_lock.isoformat() if vector_lock else None,
        "sourcePullAtUtc": source_at.isoformat() if source_at else None,
        "reasons": sorted(set(reasons)),
    }


def _locked_row_integrity(
    slate_date: str,
    expected_identities: Iterable[str],
    due_identities: Iterable[str],
    evaluate_full_slate: bool,
) -> Dict[str, Any]:
    stored = _query_stored_predictions(slate_date)
    expected = {
        _identity_from_value(value) for value in expected_identities if value
    }
    due = {_identity_from_value(value) for value in due_identities if value}
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    missing_identity_row_count = 0
    for row in stored:
        game_id = _row_identity(row)
        if game_id:
            grouped.setdefault(game_id, []).append(row)
        else:
            missing_identity_row_count += 1

    selected: List[Dict[str, Any]] = []
    for candidates in grouped.values():
        evaluated = [(row, _vector_validation(row)) for row in candidates]
        valid = [pair for pair in evaluated if pair[1].get("ok")]
        ranked = valid or evaluated
        selected.append(
            max(
                ranked,
                key=lambda pair: str(
                    pair[0].get("createdAt")
                    or pair[0].get("created_at")
                    or ""
                ),
            )[0]
        )

    checks = [_vector_validation(row) for row in selected]
    valid = [check for check in checks if check.get("ok")]
    invalid = [check for check in checks if not check.get("ok")]
    stored_identities = set(grouped)
    valid_identities = {
        _identity_from_value(check.get("gameId"))
        for check in valid
        if check.get("gameId")
    }
    duplicate_identities = sorted(
        identity for identity, rows in grouped.items() if len(rows) > 1
    )
    missing_due = sorted(due - valid_identities)
    unexpected = sorted(stored_identities - expected)
    missing = sorted(expected - stored_identities)
    authority_safe = bool(
        expected
        and due.issubset(expected)
        and not duplicate_identities
        and not unexpected
        and not invalid
        and not missing_due
        and missing_identity_row_count == 0
    )
    complete = bool(
        evaluate_full_slate
        and authority_safe
        and stored_identities == expected
        and valid_identities == expected
    )
    return {
        "evaluated": bool(evaluate_full_slate),
        "partialAuthorityEvaluated": True,
        "requiredFeatureVectorVersion": mlb_ml_clean_cohort_v1.FEATURE_SNAPSHOT_VERSION,
        "requiredFingerprintVersion": mlb_ml_clean_cohort_v1.FINGERPRINT_VERSION,
        "expectedGameCount": len(expected),
        "expectedGameIdentities": sorted(expected),
        "dueGameCount": len(due),
        "dueGameIdentities": sorted(due),
        "rawStoredRowCount": len(stored),
        "missingIdentityRowCount": missing_identity_row_count,
        "deduplicatedStoredGameCount": len(selected),
        "storedGameIdentities": sorted(stored_identities),
        "validGameIdentities": sorted(valid_identities),
        "missingGameIdentities": missing,
        "missingDueGameIdentities": missing_due,
        "unexpectedGameIdentities": unexpected,
        "duplicateGameIdentities": duplicate_identities,
        "validFingerprintCount": len(valid),
        "invalidFingerprintCount": len(invalid),
        "dueCoverageComplete": not missing_due,
        "authoritySafe": authority_safe,
        "coverageComplete": complete,
        "invalidRows": invalid,
        "checks": checks,
        "policy": (
            "Canonical LOCKED#GAME identities must be unique and a subset of "
            "the official roster; every game past its own T-45 cutoff must "
            "have a current-v3 immutable vector, and full-roster equality is "
            "required after the final cutoff."
        ),
    }


def _per_game_lock_progress(
    lock_status: Dict[str, Any],
    *,
    checked_at: datetime,
    expected_identities: Iterable[str],
) -> Dict[str, Any]:
    expected = {
        _identity_from_value(value) for value in expected_identities if value
    }
    expected_count = len(expected)
    raw_statuses = lock_status.get("perGameStatus") or []
    statuses = [row for row in raw_statuses if isinstance(row, dict)]
    due: List[Dict[str, Any]] = []
    pending: List[Dict[str, Any]] = []
    invalid: List[Dict[str, Any]] = []
    seen_game_ids: set[str] = set()
    valid_cutoffs: List[datetime] = []

    for row in statuses:
        game_id = _row_identity(row)
        lock_at = _parse_dt(row.get("scheduledLockAtUtc"))
        validation_errors: List[str] = []
        if not game_id:
            validation_errors.append("game_identity_missing")
        elif game_id in seen_game_ids:
            validation_errors.append("duplicate_game_identity")
        else:
            seen_game_ids.add(game_id)
        if not lock_at:
            validation_errors.append("scheduled_lock_at_missing_or_invalid")
        compact = {
            "gameId": game_id or None,
            "scheduledLockAtUtc": lock_at.isoformat() if lock_at else None,
            "lockStatus": row.get("lockStatus") or row.get("state"),
            "lockOutcomeRecorded": row.get("lockOutcomeRecorded") is True,
            "lockedPrediction": row.get("lockedPrediction") is True,
            "validationErrors": validation_errors,
        }
        if validation_errors:
            invalid.append(compact)
            continue
        valid_cutoffs.append(lock_at)
        if lock_at <= checked_at:
            due.append(compact)
        else:
            pending.append(compact)

    missing_due = [row for row in due if row["lockOutcomeRecorded"] is not True]
    observed = set(seen_game_ids)
    missing_identities = sorted(expected - observed)
    unexpected_identities = sorted(observed - expected)
    final_cutoff = max(valid_cutoffs) if valid_cutoffs else None
    status_complete = bool(
        expected_count > 0
        and len(statuses) == expected_count
        and len(valid_cutoffs) == expected_count
        and len(seen_game_ids) == expected_count
        and not invalid
        and not missing_identities
        and not unexpected_identities
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
        "uniqueGameCount": len(seen_game_ids),
        "gameIdentities": sorted(observed),
        "expectedGameIdentities": sorted(expected),
        "missingGameIdentities": missing_identities,
        "unexpectedGameIdentities": unexpected_identities,
        "invalidStatusCount": len(invalid),
        "invalidStatuses": invalid,
        "dueGameCount": len(due),
        "dueGameIdentities": sorted(
            str(row.get("gameId") or "") for row in due if row.get("gameId")
        ),
        "dueTerminalGameCount": len(due) - len(missing_due),
        "dueMissingGameCount": len(missing_due),
        "dueMissingGames": missing_due,
        "pendingGameCount": len(pending),
        "pendingGameIdentities": sorted(
            str(row.get("gameId") or "") for row in pending if row.get("gameId")
        ),
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
    raw_predictions = mlb_game_winner_engine.predict_all(
        slate_date, store=False, limit=500
    )
    predictions = raw_predictions if isinstance(raw_predictions, dict) else {}
    raw_lock_status = mlb_daily_pick_lock._status_payload(slate_date)
    lock_status = raw_lock_status if isinstance(raw_lock_status, dict) else {}

    blockers: List[str] = []
    roster_error: Optional[str] = None
    try:
        roster = history.verified_full_slate_manifest(pulls, slate_date)
        if not isinstance(roster, dict):
            raise TypeError("verified roster is not an object")
    except Exception as exc:
        roster = {}
        roster_error = f"{type(exc).__name__}:{exc}"
    official_games = roster.get("games") if isinstance(roster.get("games"), list) else []
    official_summary = _identity_summary(official_games)
    official_identities = official_summary["gameIdentities"]
    official_count = len(official_identities)
    official_authority_valid = bool(
        roster_error is None
        and roster.get("officialScheduleBacked") is True
        and roster.get("immutableReadbackVerified") is True
        and bool(roster.get("fullAuthorityFingerprint"))
        and official_count > 0
        and official_summary["rawCount"] == official_count
        and official_summary["validIdentityCount"] == official_count
        and not official_summary["duplicateGameIdentities"]
        and _exact_count(roster.get("fullSlateGameCount"), official_count)
        and _exact_count(roster.get("officialScheduleGameCount"), official_count)
    )
    if not official_authority_valid:
        blockers.append("OFFICIAL_SCHEDULE_AUTHORITY_INVALID")

    prediction_rows = (
        predictions.get("predictions")
        if isinstance(predictions.get("predictions"), list)
        else []
    )
    prediction_summary = _identity_summary(prediction_rows)
    prediction_identity_valid = bool(
        official_authority_valid
        and prediction_summary["gameIdentities"] == official_identities
        and prediction_summary["rawCount"] == official_count
        and prediction_summary["validIdentityCount"] == official_count
        and not prediction_summary["duplicateGameIdentities"]
        and _exact_count(predictions.get("gameCount"), official_count)
        and _exact_count(predictions.get("count"), official_count)
    )
    if predictions.get("ok") is not True:
        blockers.append("PREDICTION_ENGINE_FAILED")
    if not prediction_identity_valid:
        blockers.append("PREDICTION_ROSTER_MEMBERSHIP_MISMATCH")

    lock_official_count = lock_status.get("officialScheduleGameCount")
    lock_manifest_count = lock_status.get("manifestGameCount")
    lock_verified_count = lock_status.get("verifiedFullSlateGameCount")
    lock_authority_valid = bool(
        official_authority_valid
        and lock_status.get("officialScheduleBacked") is True
        and _exact_count(lock_status.get("gameCount"), official_count)
        and _exact_count(lock_manifest_count, official_count)
        and _exact_count(lock_verified_count, official_count)
        and _exact_count(lock_official_count, official_count)
        and str(lock_status.get("officialScheduleAuthorityFingerprint") or "")
        == str(roster.get("officialScheduleAuthorityFingerprint") or "")
        and bool(lock_status.get("officialScheduleAuthorityFingerprint"))
    )
    if lock_status.get("ok") is not True:
        blockers.append("LOCK_STATUS_FAILED")
    if not lock_authority_valid:
        blockers.append("LOCK_STATUS_ROSTER_AUTHORITY_MISMATCH")

    game_count = _count_or_zero(predictions.get("gameCount"))
    prediction_count = _count_or_zero(predictions.get("count"))
    pull_count = len(pulls)
    expected_slate_count = official_count
    per_game = _per_game_lock_progress(
        lock_status,
        checked_at=checked_at,
        expected_identities=official_identities,
    )
    if per_game["statusComplete"] is not True:
        blockers.append("PER_GAME_LOCK_STATUS_MISSING_OR_INVALID")
    if per_game["gameIdentities"] != official_identities:
        blockers.append("PER_GAME_LOCK_ROSTER_MEMBERSHIP_MISMATCH")
    integrity = _locked_row_integrity(
        slate_date,
        official_identities,
        per_game["dueGameIdentities"],
        per_game["fullSlateVectorEvaluationDue"],
    )

    if not integrity.get("authoritySafe"):
        blockers.append("CANONICAL_LOCK_ROSTER_MEMBERSHIP_MISMATCH")
    if not integrity.get("dueCoverageComplete"):
        blockers.append("LOCKED_ROWS_MISSING_VALID_FROZEN_FINGERPRINTS")

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
        if per_game["dueMissingGameCount"] > 0:
            blockers.append("LOCK_DUE_BUT_NOT_LOCKED")
        if (
            per_game["fullSlateVectorEvaluationDue"] is True
            and not integrity.get("coverageComplete")
        ):
            blockers.append("LOCKED_ROWS_MISSING_VALID_FROZEN_FINGERPRINTS")

    blockers = sorted(set(blockers))
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
        "sourceAuthority": {
            "officialSchedule": {
                **official_summary,
                "ok": official_authority_valid,
                "officialScheduleBacked": roster.get("officialScheduleBacked") is True,
                "fullSlateGameCount": roster.get("fullSlateGameCount"),
                "officialScheduleGameCount": roster.get("officialScheduleGameCount"),
                "immutableReadbackVerified": roster.get("immutableReadbackVerified") is True,
                "fullAuthorityFingerprint": roster.get("fullAuthorityFingerprint"),
                "officialScheduleAuthorityFingerprint": roster.get(
                    "officialScheduleAuthorityFingerprint"
                ),
                "error": roster_error,
            },
            "predictions": {
                **prediction_summary,
                "ok": predictions.get("ok") is True and prediction_identity_valid,
                "reportedGameCount": predictions.get("gameCount"),
                "reportedPredictionCount": predictions.get("count"),
            },
            "lockStatus": {
                "ok": lock_status.get("ok") is True and lock_authority_valid,
                "reportedGameCount": lock_status.get("gameCount"),
                "reportedManifestGameCount": lock_manifest_count,
                "reportedVerifiedFullSlateGameCount": lock_verified_count,
                "reportedOfficialScheduleGameCount": lock_official_count,
                "officialScheduleBacked": lock_status.get("officialScheduleBacked") is True,
                "officialScheduleAuthorityFingerprint": lock_status.get(
                    "officialScheduleAuthorityFingerprint"
                ),
            },
            "canonicalLocks": {
                "ok": integrity.get("authoritySafe") is True,
                "storedGameIdentities": integrity.get("storedGameIdentities") or [],
                "dueGameIdentities": integrity.get("dueGameIdentities") or [],
                "missingDueGameIdentities": integrity.get(
                    "missingDueGameIdentities"
                )
                or [],
                "unexpectedGameIdentities": integrity.get(
                    "unexpectedGameIdentities"
                )
                or [],
                "duplicateGameIdentities": integrity.get(
                    "duplicateGameIdentities"
                )
                or [],
                "fullRosterEqualityRequired": per_game[
                    "fullSlateVectorEvaluationDue"
                ],
                "fullRosterEqualitySatisfied": integrity.get(
                    "coverageComplete"
                )
                is True,
            },
            "identitySetsEqual": bool(
                official_authority_valid
                and prediction_identity_valid
                and lock_authority_valid
                and per_game["statusComplete"] is True
                and per_game["gameIdentities"] == official_identities
                and integrity.get("authoritySafe") is True
            ),
        },
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
