from __future__ import annotations

import json
import math
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from botocore.exceptions import ClientError

try:
    import mlb_ml_runtime_install_v3

    _raw_runtime_status = mlb_ml_runtime_install_v3.install()
except Exception as exc:
    _raw_runtime_status = {
        "applied": False,
        "ok": False,
        "steps": {},
        "errors": [str(exc)],
    }

_REQUIRED_RUNTIME_STEPS = {
    "accuracyTargetsSeparated",
    "legacyReliabilityOverlaySafety",
    "sourceHonestFundamentals",
    "sourceHonestFundamentalsV2",
    "legacyV1ChampionRuntimeInstalledForShadowDiagnostics",
    "legacyV1AuthorityDisabled",
    "v2ShadowManualFirst",
    "officialSemanticsFinalized",
    "exactCleanCohortVectorPatch",
    "officialFreezeBridge",
    "immutableFeatureFreeze",
    "immutableLockedStorageAuthority",
    "canonicalLockedStorageFinalizer",
    "lastPrelockPromotionAuthority",
    "canonicalProbabilityAndPersistedPrelockAuthority",
    "providerNeutralCalibrationAndActionability",
    "legacyFinalGateDisabled",
}
if isinstance(_raw_runtime_status, dict):
    ML_RUNTIME_INSTALL_STATUS = dict(_raw_runtime_status)
else:
    ML_RUNTIME_INSTALL_STATUS = {
        "applied": False,
        "ok": False,
        "steps": {},
        "errors": [
            "mlb_ml_runtime_install_v3.install() returned a non-dictionary status"
        ],
    }
_runtime_steps = ML_RUNTIME_INSTALL_STATUS.get("steps")
if not isinstance(_runtime_steps, dict):
    _runtime_steps = {}
    ML_RUNTIME_INSTALL_STATUS["ok"] = False
    ML_RUNTIME_INSTALL_STATUS["errors"] = list(
        ML_RUNTIME_INSTALL_STATUS.get("errors") or []
    ) + ["mlb_ml_runtime_install_v3.install() returned invalid step status"]
_missing_runtime_steps = sorted(
    name for name in _REQUIRED_RUNTIME_STEPS if _runtime_steps.get(name) is not True
)
_expected_runtime_version = getattr(
    globals().get("mlb_ml_runtime_install_v3"), "VERSION", None
)
if ML_RUNTIME_INSTALL_STATUS.get("applied") is not True:
    ML_RUNTIME_INSTALL_STATUS["ok"] = False
if ML_RUNTIME_INSTALL_STATUS.get("errors"):
    ML_RUNTIME_INSTALL_STATUS["ok"] = False
if (
    not _expected_runtime_version
    or ML_RUNTIME_INSTALL_STATUS.get("version") != _expected_runtime_version
):
    ML_RUNTIME_INSTALL_STATUS["ok"] = False
    ML_RUNTIME_INSTALL_STATUS["expectedVersion"] = _expected_runtime_version
if _missing_runtime_steps:
    ML_RUNTIME_INSTALL_STATUS["ok"] = False
    ML_RUNTIME_INSTALL_STATUS["missingRequiredSteps"] = _missing_runtime_steps

import mlb_daily_pick_lock
import mlb_daily_lock_coverage_patch
import mlb_daily_lock_ml_vector_preservation_patch
import mlb_daily_per_game_lock_patch

LOCK_RUNTIME_FIX_VERSION = "MLB-LOCK-RUNTIME-FIX-v5-official-schedule-lifecycle-vector-separation"
LOCK_EXECUTION_LEASE_VERSION = "MLB-LOCK-EXECUTION-LEASE-v1"
LOCK_EXECUTION_LEASE_PK = "MLB_LOCK_EXECUTION#V1"
LOCK_EXECUTION_LEASE_SK = "LEASE"
LOCK_EXECUTION_LEASE_RECORD_TYPE = "mlb_lock_execution_lease_v1"
LOCK_EXECUTION_LEASE_REQUIRED_SECONDS = 360
LOCK_EXECUTION_TIMEOUT_SAFETY_MARGIN_SECONDS = 60
try:
    LOCK_EXECUTION_LEASE_SECONDS = int(
        os.environ.get("MLB_LOCK_EXECUTION_LEASE_SECONDS", "360")
    )
except (TypeError, ValueError):
    LOCK_EXECUTION_LEASE_SECONDS = -1

mlb_daily_lock_coverage_patch.apply(mlb_daily_pick_lock)
ML_VECTOR_PRESERVATION_STATUS = mlb_daily_lock_ml_vector_preservation_patch.apply(mlb_daily_pick_lock)
mlb_daily_per_game_lock_patch.apply(mlb_daily_pick_lock)
_expected_attempt_diagnostics_version = getattr(
    mlb_daily_per_game_lock_patch, "ATTEMPT_DIAGNOSTICS_VERSION", None
)
_attempt_diagnostics_version = getattr(
    mlb_daily_pick_lock, "MLB_PER_GAME_LOCK_ATTEMPT_DIAGNOSTICS_VERSION", None
)
_attempt_diagnostics_ready = bool(
    _expected_attempt_diagnostics_version
    and _attempt_diagnostics_version == _expected_attempt_diagnostics_version
)
_expected_promotion_version = getattr(
    mlb_daily_per_game_lock_patch, "PROMOTION_POLICY_VERSION", None
)
_promotion_version = getattr(
    mlb_daily_pick_lock, "MLB_LAST_PRELOCK_PROMOTION_VERSION", None
)
_promotion_ready = bool(
    _expected_promotion_version
    and _promotion_version == _expected_promotion_version
)
_payload_fingerprint_version = getattr(
    mlb_daily_per_game_lock_patch, "PAYLOAD_FINGERPRINT_VERSION", None
)
_prediction_engine = getattr(mlb_daily_pick_lock, "mlb_game_winner_engine", None)
_history_contract = getattr(mlb_daily_pick_lock, "history", None)
_writer_payload_fingerprint_version = getattr(
    _prediction_engine, "PAYLOAD_FINGERPRINT_VERSION", None
)
_history_payload_fingerprint_version = getattr(
    _history_contract, "CANONICAL_PAYLOAD_FINGERPRINT_VERSION", None
)
_payload_fingerprint_ready = bool(
    _payload_fingerprint_version
    and _payload_fingerprint_version == _writer_payload_fingerprint_version
    and _payload_fingerprint_version == _history_payload_fingerprint_version
    and callable(getattr(_history_contract, "canonical_payload_fingerprint", None))
)
_expected_readiness_version = getattr(mlb_daily_per_game_lock_patch, "READINESS_VERSION", None)
_expected_lock_outcome_version = getattr(mlb_daily_per_game_lock_patch, "LOCK_OUTCOME_VERSION", None)
_expected_playability_version = getattr(mlb_daily_per_game_lock_patch, "RELEASE_ASSESSMENT_VERSION", None)
_readiness_version = getattr(mlb_daily_pick_lock, "MLB_LOCK_READINESS_VERSION", None)
_lock_outcome_version = getattr(mlb_daily_pick_lock, "MLB_LOCK_OUTCOME_VERSION", None)
_playability_version = getattr(mlb_daily_pick_lock, "MLB_PLAYABILITY_ASSESSMENT_VERSION", None)
_source_window_stabilization_seconds = getattr(
    mlb_daily_pick_lock,
    "MLB_LOCK_SOURCE_WINDOW_STABILIZATION_SECONDS",
    None,
)
_expected_lock_execution_lease_version = getattr(
    mlb_daily_per_game_lock_patch,
    "LOCK_EXECUTION_LEASE_VERSION",
    None,
)
_lock_execution_lease_version = getattr(
    mlb_daily_pick_lock,
    "MLB_LOCK_EXECUTION_LEASE_VERSION",
    None,
)
_lock_execution_lease_seconds = getattr(
    mlb_daily_pick_lock,
    "MLB_LOCK_EXECUTION_LEASE_SECONDS",
    None,
)
_lock_execution_lease_scope = getattr(
    mlb_daily_pick_lock,
    "MLB_LOCK_EXECUTION_LEASE_SCOPE",
    None,
)
_lock_execution_lease_ready = bool(
    _expected_lock_execution_lease_version
    and _lock_execution_lease_version == _expected_lock_execution_lease_version
    and _lock_execution_lease_seconds == 360
    and _lock_execution_lease_scope == "global_all_mutating_lock_invocations"
    and getattr(
        mlb_daily_pick_lock,
        "MLB_LOCK_EXECUTION_LEGACY_ROLLOUT_BRIDGE",
        False,
    )
    is True
)
_lifecycle_ready = bool(
    _expected_readiness_version
    and _readiness_version == _expected_readiness_version
    and _expected_lock_outcome_version
    and _lock_outcome_version == _expected_lock_outcome_version
    and _expected_playability_version
    and _playability_version == _expected_playability_version
    and _source_window_stabilization_seconds == 0
)
_selection_vector_separation_ready = bool(
    ML_VECTOR_PRESERVATION_STATUS.get("selectionLockIndependentOfTrainingVector") is True
)
_official_schedule_authority_version = getattr(
    mlb_daily_per_game_lock_patch,
    "OFFICIAL_SCHEDULE_AUTHORITY_VERSION",
    None,
)
_official_schedule_authority_ready = bool(
    _official_schedule_authority_version
    == "MLB-OFFICIAL-SCHEDULE-AUTHORITY-v1-statsapi-exact-date"
    and callable(getattr(_history_contract, "verified_full_slate_manifest", None))
)
PER_GAME_LOCK_STATUS = {
    "ok": bool(
        getattr(mlb_daily_pick_lock, "_INQSI_MLB_DAILY_PER_GAME_LOCK_V1", False)
        and _attempt_diagnostics_ready
        and _promotion_ready
        and _payload_fingerprint_ready
        and _lifecycle_ready
        and _selection_vector_separation_ready
        and _official_schedule_authority_ready
        and _lock_execution_lease_ready
        and LOCK_EXECUTION_LEASE_SECONDS
        == LOCK_EXECUTION_LEASE_REQUIRED_SECONDS
    ),
    "version": getattr(mlb_daily_pick_lock, "MLB_DAILY_PER_GAME_LOCK_VERSION", None),
    "policy": getattr(mlb_daily_pick_lock, "LOCK_POLICY", None),
    "failClosed": True,
    "canonicalGameWriteAtOwnTMinus45": True,
    "lastPrelockAtCutoffBecomesFinal": _promotion_ready,
    "modelOrSignalRecomputedAtLock": False,
    "lastPrelockPromotionVersion": _promotion_version,
    "expectedLastPrelockPromotionVersion": _expected_promotion_version,
    "fixVersion": LOCK_RUNTIME_FIX_VERSION,
    "candidatePayloadFingerprintVersion": _payload_fingerprint_version,
    "writerPayloadFingerprintVersion": _writer_payload_fingerprint_version,
    "historyPayloadFingerprintVersion": _history_payload_fingerprint_version,
    "candidatePayloadFingerprintDdbReadCanonical": _payload_fingerprint_ready,
    "explicitMlRuntimeInstall": True,
    "durableAttemptDiagnostics": _attempt_diagnostics_ready,
    "attemptDiagnosticsVersion": _attempt_diagnostics_version,
    "expectedAttemptDiagnosticsVersion": _expected_attempt_diagnostics_version,
    "readinessCheckpointsAtTMinus60AndTMinus50": _lifecycle_ready,
    "readinessVersion": _readiness_version,
    "lockOutcomeStatusSeparateFromPrediction": _lifecycle_ready,
    "lockOutcomeVersion": _lock_outcome_version,
    "latePlayabilityAssessmentCannotRewriteSelection": _lifecycle_ready,
    "playabilityAssessmentVersion": _playability_version,
    "sourceWindowStabilizationSeconds": _source_window_stabilization_seconds,
    "doubleheaderGame2EventDrivenPlayabilityRecheck": _lifecycle_ready,
    "officialScheduleAuthorityRequired": _official_schedule_authority_ready,
    "officialScheduleAuthorityVersion": _official_schedule_authority_version,
    "selectionLockIndependentOfTrainingVector": _selection_vector_separation_ready,
    "globalAllMutatingLockExecutionLease": _lock_execution_lease_ready,
    "lockExecutionLeaseVersion": _lock_execution_lease_version,
    "lockExecutionLeaseSeconds": _lock_execution_lease_seconds,
    "legacyRuntimeLeaseRolloutBridge": _lock_execution_lease_ready,
    "lockExecutionConcurrency": {
        "version": LOCK_EXECUTION_LEASE_VERSION,
        "strategy": "dynamodb_conditional_lease",
        "scope": "global_mlb_lock_execution",
        "sharedLeaseKey": True,
        "leaseSeconds": LOCK_EXECUTION_LEASE_SECONDS,
        "requiredLeaseSeconds": LOCK_EXECUTION_LEASE_REQUIRED_SECONDS,
        "lambdaTimeoutSeconds": 300,
        "timeoutSafetyMarginSeconds": (
            LOCK_EXECUTION_TIMEOUT_SAFETY_MARGIN_SECONDS
        ),
        "expiredLeaseReclaim": True,
        "ownerConditionalRelease": True,
        "reservedLambdaConcurrencyRequired": False,
    },
}
ADMIN_TOKEN = os.environ.get("INQSI_ADMIN_API_TOKEN", "")


class LockExecutionLeaseUnavailable(RuntimeError):
    """Another mutating lock invocation currently owns the global lease."""


class LockExecutionLeaseOwnershipConflict(RuntimeError):
    """The caller no longer owns the global lease it attempted to release."""


class LockHttpMethodInvalid(ValueError):
    """An HTTP-shaped event did not provide one unambiguous method."""


def _resp(status: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json",
            "access-control-allow-origin": "*",
            "access-control-allow-headers": "content-type,authorization,x-inqsi-admin-token",
            "access-control-allow-methods": "GET,POST,OPTIONS",
        },
        "body": json.dumps(body),
    }


def _is_scheduled(event: Dict[str, Any]) -> bool:
    # EventBridge inputs have neither HTTP field. Treat the presence of either
    # field as HTTP-shaped even when its value is empty so malformed requests
    # cannot fall through to the unauthenticated scheduled writer path.
    return "httpMethod" not in event and "requestContext" not in event


def _http_method(event: Dict[str, Any]) -> Optional[str]:
    """Resolve REST API v1 and HTTP API v2 methods without ambiguity."""

    top_level = str(event.get("httpMethod") or "").strip().upper()
    has_top_level = "httpMethod" in event
    has_request_context = "requestContext" in event
    nested = ""
    if has_request_context:
        request_context = event.get("requestContext")
        if not isinstance(request_context, dict):
            raise LockHttpMethodInvalid("REQUEST_CONTEXT_NOT_OBJECT")
        http_context = request_context.get("http")
        if http_context is not None:
            if not isinstance(http_context, dict):
                raise LockHttpMethodInvalid("REQUEST_CONTEXT_HTTP_NOT_OBJECT")
            nested = str(http_context.get("method") or "").strip().upper()

    if top_level and nested and top_level != nested:
        raise LockHttpMethodInvalid("CONFLICTING_HTTP_METHODS")
    method = top_level or nested
    if has_request_context and not method:
        raise LockHttpMethodInvalid("HTTP_METHOD_MISSING")
    if has_top_level and not method:
        raise LockHttpMethodInvalid("HTTP_METHOD_EMPTY")
    return method or None


def _normalize_http_event(
    event: Dict[str, Any], method: Optional[str]
) -> Dict[str, Any]:
    if method is None:
        return event
    normalized = dict(event)
    # The delegated lock implementation consumes the REST API v1 field. Add it
    # for HTTP API v2 events so a derived GET can never be mistaken for the
    # delegate's method-less scheduled mutation path.
    normalized["httpMethod"] = method
    return normalized


def _failure_response(event: Dict[str, Any], status: int, body: Dict[str, Any]) -> Dict[str, Any]:
    if _is_scheduled(event):
        raise RuntimeError(
            f"MLB_SCHEDULED_LOCK_PREREQUISITE_FAILED:{json.dumps(body, default=str, sort_keys=True)}"
        )
    return _resp(status, body)


def _raise_scheduled_delegate_failure(event: Dict[str, Any], response: Any) -> None:
    if not _is_scheduled(event) or not isinstance(response, dict):
        return
    body = response.get("body")
    try:
        payload = json.loads(body) if isinstance(body, str) else dict(body or {})
    except Exception:
        payload = {"rawBody": body}
    try:
        status_code = int(response.get("statusCode") or 200)
    except Exception:
        status_code = 500
    if status_code >= 400 or payload.get("ok") is False:
        raise RuntimeError(
            f"MLB_SCHEDULED_LOCK_FAILED:{json.dumps(payload, default=str, sort_keys=True)}"
        )


def _header(event: Dict[str, Any], name: str) -> str:
    headers = event.get("headers") or {}
    if isinstance(headers, dict):
        for key, value in headers.items():
            if str(key).lower() == name.lower():
                return str(value or "")
    return ""


def _auth_error(
    event: Dict[str, Any], method: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    method = method if method is not None else _http_method(event)
    # EventBridge scheduled invocations have no HTTP method and remain allowed.
    # GET status/today endpoints are read-only and remain public.
    if _is_scheduled(event):
        return None
    if method in {"GET", "OPTIONS"}:
        return None
    if not ADMIN_TOKEN:
        return _resp(500, {"ok": False, "sport": "mlb", "error": "INQSI_ADMIN_API_TOKEN_NOT_CONFIGURED"})
    token = _header(event, "x-inqsi-admin-token").strip()
    auth = _header(event, "authorization").strip()
    if auth.lower().startswith("bearer "):
        auth = auth.split(" ", 1)[1].strip()
    if token == ADMIN_TOKEN or auth == ADMIN_TOKEN:
        return None
    return _resp(401, {"ok": False, "sport": "mlb", "error": "ADMIN_TOKEN_REQUIRED"})


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _error_code(exc: BaseException) -> str:
    if isinstance(exc, ClientError):
        return str((exc.response.get("Error") or {}).get("Code") or "ClientError")
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        return str((response.get("Error") or {}).get("Code") or type(exc).__name__)
    return type(exc).__name__


def _lease_key() -> Dict[str, str]:
    return {"PK": LOCK_EXECUTION_LEASE_PK, "SK": LOCK_EXECUTION_LEASE_SK}


def _execution_mode(
    event: Dict[str, Any], method: Optional[str] = None
) -> Optional[str]:
    if _is_scheduled(event):
        return "scheduled"
    method = method if method is not None else _http_method(event)
    if method == "POST":
        return "manual"
    return None


def _slate_date_et(event: Dict[str, Any]) -> str:
    payload_reader = getattr(mlb_daily_pick_lock, "_payload", None)
    payload = payload_reader(event) if callable(payload_reader) else {}
    if not isinstance(payload, dict):
        payload = {}
    for key in ("slateDateEt", "slate_date", "slateDate", "date"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    today_reader = getattr(mlb_daily_pick_lock, "_today_et", None)
    if not callable(today_reader):
        raise RuntimeError("MLB_LOCK_SLATE_DATE_AUTHORITY_NOT_AVAILABLE")
    return str(today_reader())


def _lease_owner(context: Any, mode: str) -> str:
    request_id = str(getattr(context, "aws_request_id", "") or "").strip()
    return f"{mode}:{request_id or uuid.uuid4().hex}"


def _validate_lease_duration(context: Any) -> None:
    if LOCK_EXECUTION_LEASE_SECONDS != LOCK_EXECUTION_LEASE_REQUIRED_SECONDS:
        raise RuntimeError(
            "MLB_LOCK_EXECUTION_LEASE_DURATION_MISMATCH:"
            f"expected={LOCK_EXECUTION_LEASE_REQUIRED_SECONDS}:"
            f"actual={LOCK_EXECUTION_LEASE_SECONDS}"
        )
    remaining_reader = getattr(context, "get_remaining_time_in_millis", None)
    if not callable(remaining_reader):
        return
    remaining_seconds = math.ceil(max(0, int(remaining_reader())) / 1000)
    required_seconds = (
        remaining_seconds + LOCK_EXECUTION_TIMEOUT_SAFETY_MARGIN_SECONDS
    )
    if LOCK_EXECUTION_LEASE_SECONDS < required_seconds:
        raise RuntimeError(
            "MLB_LOCK_EXECUTION_LEASE_TIMEOUT_BOUND_FAILED:"
            f"remaining={remaining_seconds}:"
            f"margin={LOCK_EXECUTION_TIMEOUT_SAFETY_MARGIN_SECONDS}:"
            f"lease={LOCK_EXECUTION_LEASE_SECONDS}"
        )


def _acquire_execution_lease(
    *, mode: str, slate_date_et: str, owner: str
) -> Dict[str, Any]:
    table = getattr(mlb_daily_pick_lock, "TABLE", None)
    if table is None:
        raise RuntimeError("MLB_LOCK_EXECUTION_LEASE_TABLE_NOT_CONFIGURED")
    acquired_at = _utc_now()
    expires_at = acquired_at + timedelta(seconds=LOCK_EXECUTION_LEASE_SECONDS)
    item = {
        **_lease_key(),
        "record_type": LOCK_EXECUTION_LEASE_RECORD_TYPE,
        "lease_version": LOCK_EXECUTION_LEASE_VERSION,
        "lease_owner": owner,
        "execution_mode": mode,
        "slate_date_et": slate_date_et,
        "lease_acquired_at_utc": acquired_at.isoformat(),
        "lease_expires_at_utc": expires_at.isoformat(),
        # Round upward. Flooring a fractional timestamp can shorten a
        # 360-second lease below the Lambda timeout plus its 60-second margin.
        "lease_expires_at_epoch": math.ceil(expires_at.timestamp()),
    }
    try:
        table.put_item(
            Item=item,
            ConditionExpression=(
                "attribute_not_exists(PK) OR "
                "attribute_not_exists(lease_expires_at_epoch) OR "
                "lease_expires_at_epoch <= :now"
            ),
            ExpressionAttributeValues={":now": int(acquired_at.timestamp())},
        )
    except ClientError as exc:
        if _error_code(exc) == "ConditionalCheckFailedException":
            raise LockExecutionLeaseUnavailable(
                "MLB_LOCK_EXECUTION_LEASE_ALREADY_HELD"
            ) from exc
        raise
    return item


def _release_execution_lease(owner: str) -> None:
    table = getattr(mlb_daily_pick_lock, "TABLE", None)
    if table is None:
        raise RuntimeError("MLB_LOCK_EXECUTION_LEASE_TABLE_NOT_CONFIGURED")
    try:
        table.delete_item(
            Key=_lease_key(),
            ConditionExpression=(
                "lease_owner = :owner AND record_type = :record_type AND "
                "lease_version = :lease_version"
            ),
            ExpressionAttributeValues={
                ":owner": owner,
                ":record_type": LOCK_EXECUTION_LEASE_RECORD_TYPE,
                ":lease_version": LOCK_EXECUTION_LEASE_VERSION,
            },
        )
    except ClientError as exc:
        if _error_code(exc) == "ConditionalCheckFailedException":
            raise LockExecutionLeaseOwnershipConflict(
                "MLB_LOCK_EXECUTION_LEASE_OWNER_CHANGED"
            ) from exc
        raise


def _lease_status() -> Dict[str, Any]:
    table = getattr(mlb_daily_pick_lock, "TABLE", None)
    if table is None:
        return {"statusReadOk": False, "active": None, "reason": "TABLE_NOT_CONFIGURED"}
    try:
        item = (
            table.get_item(Key=_lease_key(), ConsistentRead=True).get("Item")
            or {}
        )
    except BaseException as exc:
        return {
            "statusReadOk": False,
            "active": None,
            "reason": "STATUS_READ_FAILED",
            "errorCode": _error_code(exc),
        }
    if not item:
        return {"statusReadOk": True, "active": False}
    try:
        expires_epoch = int(item.get("lease_expires_at_epoch"))
    except (TypeError, ValueError):
        expires_epoch = None
    return {
        "statusReadOk": True,
        "active": bool(
            expires_epoch is not None
            and expires_epoch > int(_utc_now().timestamp())
        ),
        "executionMode": item.get("execution_mode"),
        "slateDateEt": item.get("slate_date_et"),
        "expiresAtUtc": item.get("lease_expires_at_utc"),
        "expiresAtEpoch": expires_epoch,
        "ownerPresent": bool(item.get("lease_owner")),
    }


def _concurrency_control(
    *,
    mode: Optional[str],
    slate_date_et: Optional[str],
    acquired: bool = False,
    released: bool = False,
    skipped: bool = False,
    active_lease: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    result = {
        "version": LOCK_EXECUTION_LEASE_VERSION,
        "strategy": "dynamodb_conditional_lease",
        "scope": "global_mlb_lock_execution",
        "leaseSeconds": LOCK_EXECUTION_LEASE_SECONDS,
        "timeoutSafetyMarginSeconds": (
            LOCK_EXECUTION_TIMEOUT_SAFETY_MARGIN_SECONDS
        ),
        "expiredLeaseReclaim": True,
        "ownerConditionalRelease": True,
        "reservedLambdaConcurrencyRequired": False,
        "executionMode": mode,
        "slateDateEt": slate_date_et,
        "leaseAcquired": acquired,
        "leaseReleased": released,
        "overlapSkipped": skipped,
        "nextFreshScheduleIsRetry": mode == "scheduled",
    }
    if active_lease is not None:
        result["activeLease"] = active_lease
    return result


def _attach_preservation_status(
    response: Any, *, concurrency: Optional[Dict[str, Any]] = None
) -> Any:
    if not isinstance(response, dict):
        return response
    out = dict(response)
    body = out.get("body")
    try:
        payload = json.loads(body) if isinstance(body, str) else dict(body or {})
    except Exception:
        payload = {"rawBody": body}
    if isinstance(payload, dict):
        payload["mlRuntimeInstallation"] = ML_RUNTIME_INSTALL_STATUS
        payload["mlLockVectorPreservation"] = ML_VECTOR_PRESERVATION_STATUS
        payload["perGameLockInstallation"] = PER_GAME_LOCK_STATUS
        if concurrency is not None:
            payload["lockExecutionConcurrency"] = concurrency
        out["body"] = json.dumps(payload)
    return out


def _response_failed(response: Any) -> bool:
    if not isinstance(response, dict):
        return True
    try:
        status_code = int(response.get("statusCode") or 200)
    except (TypeError, ValueError):
        status_code = 500
    body = response.get("body")
    try:
        payload = json.loads(body) if isinstance(body, str) else dict(body or {})
    except Exception:
        payload = {}
    return bool(status_code >= 400 or payload.get("ok") is False)


def lambda_handler(event, context):
    event = event or {}
    try:
        method = _http_method(event)
    except LockHttpMethodInvalid as exc:
        return _resp(
            400,
            {
                "ok": False,
                "sport": "mlb",
                "error": "MLB_LOCK_HTTP_METHOD_INVALID",
                "reason": str(exc),
            },
        )
    event = _normalize_http_event(event, method)
    if method is not None and method not in {"GET", "POST", "OPTIONS"}:
        return _resp(
            405,
            {
                "ok": False,
                "sport": "mlb",
                "error": "MLB_LOCK_HTTP_METHOD_NOT_ALLOWED",
                "method": method,
            },
        )
    if method == "OPTIONS":
        return _resp(200, {
            "ok": True,
            "mlRuntimeInstallation": ML_RUNTIME_INSTALL_STATUS,
            "mlLockVectorPreservation": ML_VECTOR_PRESERVATION_STATUS,
            "perGameLockInstallation": PER_GAME_LOCK_STATUS,
        })
    if ML_RUNTIME_INSTALL_STATUS.get("ok") is not True:
        return _failure_response(
            event,
            500,
            {
                "ok": False,
                "sport": "mlb",
                "error": "MLB_ML_LOCK_RUNTIME_NOT_READY",
                "status": ML_RUNTIME_INSTALL_STATUS,
            },
        )
    if ML_VECTOR_PRESERVATION_STATUS.get("ok") is not True:
        return _failure_response(
            event,
            500,
            {
                "ok": False,
                "sport": "mlb",
                "error": "MLB_DAILY_LOCK_ML_VECTOR_PRESERVATION_NOT_INSTALLED",
                "status": ML_VECTOR_PRESERVATION_STATUS,
            },
        )
    if PER_GAME_LOCK_STATUS.get("ok") is not True:
        return _failure_response(
            event,
            500,
            {
                "ok": False,
                "sport": "mlb",
                "error": "MLB_DAILY_PER_GAME_LOCK_NOT_INSTALLED",
                "status": PER_GAME_LOCK_STATUS,
            },
        )
    auth_error = _auth_error(event, method)
    if auth_error is not None:
        return auth_error

    mode = _execution_mode(event, method)
    if mode is None:
        response = _attach_preservation_status(
            mlb_daily_pick_lock.lambda_handler(event, context),
            concurrency=_concurrency_control(
                mode=None,
                slate_date_et=None,
            ),
        )
        _raise_scheduled_delegate_failure(event, response)
        return response

    try:
        _validate_lease_duration(context)
        slate_date_et = _slate_date_et(event)
    except BaseException as exc:
        return _failure_response(
            event,
            500,
            {
                "ok": False,
                "sport": "mlb",
                "error": "MLB_LOCK_EXECUTION_LEASE_VALIDATION_FAILED",
                "errorCode": _error_code(exc),
            },
        )

    owner = _lease_owner(context, mode)
    try:
        _acquire_execution_lease(
            mode=mode,
            slate_date_et=slate_date_et,
            owner=owner,
        )
    except LockExecutionLeaseUnavailable:
        concurrency = _concurrency_control(
            mode=mode,
            slate_date_et=slate_date_et,
            skipped=True,
            active_lease=_lease_status(),
        )
        if mode == "manual":
            return _attach_preservation_status(
                _resp(
                    409,
                    {
                        "ok": False,
                        "sport": "mlb",
                        "error": "MLB_LOCK_EXECUTION_ALREADY_RUNNING",
                        "skipped": True,
                        "retryable": True,
                        "mutatingRunAttempted": False,
                        "slateDateEt": slate_date_et,
                    },
                ),
                concurrency=concurrency,
            )
        return _attach_preservation_status(
            _resp(
                200,
                {
                    "ok": True,
                    "sport": "mlb",
                    "status": "SKIPPED_OVERLAPPING_LOCK_EXECUTION",
                    "skipped": True,
                    "mutatingRunAttempted": False,
                    "nextFreshScheduleIsRetry": True,
                    "slateDateEt": slate_date_et,
                },
            ),
            concurrency=concurrency,
        )
    except BaseException as exc:
        return _failure_response(
            event,
            500,
            {
                "ok": False,
                "sport": "mlb",
                "error": "MLB_LOCK_EXECUTION_LEASE_ACQUIRE_FAILED",
                "errorCode": _error_code(exc),
                "mutatingRunAttempted": False,
            },
        )

    response: Any = None
    primary_error: Optional[BaseException] = None
    release_error: Optional[BaseException] = None
    try:
        response = mlb_daily_pick_lock.lambda_handler(event, context)
        _raise_scheduled_delegate_failure(event, response)
    except BaseException as exc:
        primary_error = exc
    finally:
        try:
            _release_execution_lease(owner)
        except BaseException as exc:
            release_error = exc

    if primary_error is not None:
        if release_error is not None:
            print(
                json.dumps(
                    {
                        "event": "MLB_LOCK_EXECUTION_LEASE_RELEASE_FAILED_AFTER_PRIMARY_ERROR",
                        "releaseErrorCode": _error_code(release_error),
                    },
                    sort_keys=True,
                )
            )
        raise primary_error
    if release_error is not None:
        if _response_failed(response):
            print(
                json.dumps(
                    {
                        "event": "MLB_LOCK_EXECUTION_LEASE_RELEASE_FAILED_AFTER_FAILED_RESPONSE",
                        "releaseErrorCode": _error_code(release_error),
                    },
                    sort_keys=True,
                )
            )
            return _attach_preservation_status(
                response,
                concurrency=_concurrency_control(
                    mode=mode,
                    slate_date_et=slate_date_et,
                    acquired=True,
                    released=False,
                ),
            )
        return _failure_response(
            event,
            500,
            {
                "ok": False,
                "sport": "mlb",
                "error": "MLB_LOCK_EXECUTION_LEASE_RELEASE_FAILED",
                "errorCode": _error_code(release_error),
            },
        )

    response = _attach_preservation_status(
        response,
        concurrency=_concurrency_control(
            mode=mode,
            slate_date_et=slate_date_et,
            acquired=True,
            released=True,
        ),
    )
    return response
