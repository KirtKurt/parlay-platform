from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

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
}
ADMIN_TOKEN = os.environ.get("INQSI_ADMIN_API_TOKEN", "")


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
    return not (event.get("httpMethod") or event.get("requestContext"))


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


def _auth_error(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    method = (event.get("httpMethod") or "").upper()
    # EventBridge scheduled invocations have no HTTP method and remain allowed.
    # GET status/today endpoints are read-only and remain public.
    if not (event.get("httpMethod") or event.get("requestContext")):
        return None
    if method in {"", "GET", "OPTIONS"}:
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


def _attach_preservation_status(response: Any) -> Any:
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
        out["body"] = json.dumps(payload)
    return out


def lambda_handler(event, context):
    event = event or {}
    if (event.get("httpMethod") or "").upper() == "OPTIONS":
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
    auth_error = _auth_error(event)
    if auth_error is not None:
        return auth_error
    response = _attach_preservation_status(mlb_daily_pick_lock.lambda_handler(event, context))
    _raise_scheduled_delegate_failure(event, response)
    return response
