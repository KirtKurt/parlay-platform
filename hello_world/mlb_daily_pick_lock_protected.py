from __future__ import annotations

import hashlib
import json
import math
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Mapping, Optional

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
import mlb_terminal_lifecycle_count_reconciliation as lifecycle_counts

LOCK_RUNTIME_FIX_VERSION = "MLB-LOCK-RUNTIME-FIX-v5-official-schedule-lifecycle-vector-separation"
LOCK_EXECUTION_LEASE_VERSION = "MLB-LOCK-EXECUTION-LEASE-v1"
LOCK_EXECUTION_LEASE_PK = "MLB_LOCK_EXECUTION#V1"
LOCK_EXECUTION_LEASE_SK = "LEASE"
LOCK_EXECUTION_LEASE_RECORD_TYPE = "mlb_lock_execution_lease_v1"
LOCK_EXECUTION_LEASE_REQUIRED_SECONDS = 960
LOCK_EXECUTION_TIMEOUT_SAFETY_MARGIN_SECONDS = 60
COOPERATIVE_TERMINAL_REPLAY_VERSION = (
    "MLB-COOPERATIVE-TERMINAL-REPLAY-v1-eventbridge-owner-handoff"
)
COOPERATIVE_TERMINAL_REPLAY_RUN = (
    "prospective_terminal_backlog_reconciliation_v5"
)
COOPERATIVE_TERMINAL_REPLAY_SK = "COOPERATIVE_TERMINAL_REPLAY#V1"
COOPERATIVE_TERMINAL_REPLAY_RECORD_TYPE = (
    "mlb_cooperative_terminal_replay_v1"
)
COOPERATIVE_REPLAY_QUEUED = "QUEUED"
COOPERATIVE_REPLAY_CLAIMED = "CLAIMED"
COOPERATIVE_REPLAY_COMPLETED = "COMPLETED"
COOPERATIVE_REPLAY_ACKNOWLEDGED = "ACKNOWLEDGED"
COOPERATIVE_REPLAY_REVIEW_REQUIRED = "REVIEW_REQUIRED"
# The normal current-slate run always executes first.  A historical replay is
# attempted only when Lambda still has a conservative ten-minute execution
# budget plus the same one-minute release margin used by the global lease.
COOPERATIVE_REPLAY_EXECUTION_BUDGET_SECONDS = 600
COOPERATIVE_TERMINAL_CHUNK_V3_VERSION = (
    "MLB-COOPERATIVE-TERMINAL-CHUNK-v3-bounded-proof-lease-handoff"
)
COOPERATIVE_TERMINAL_CHUNK_VERSION = (
    "MLB-COOPERATIVE-TERMINAL-CHUNK-v4-valid-prelock-quarantine"
)
COOPERATIVE_REPLAY_PERMANENT_REVIEW_ERRORS = frozenset(
    {
        "PRELOCK_CANDIDATE_REQUIRES_REVIEW",
        "COOPERATIVE_TERMINAL_CHUNK_V3_MIGRATION_NOT_ZERO_WORK",
        "COOPERATIVE_TERMINAL_CHUNK_NOT_COMPLETE",
        "COOPERATIVE_TERMINAL_CHUNK_NOT_EXACT_HISTORICAL_DATE",
        "COOPERATIVE_TERMINAL_CHUNK_SLATE_DATE_INVALID",
        "COOPERATIVE_TERMINAL_CHUNK_GAME_IDENTITY_NOT_CALLABLE",
        "COOPERATIVE_TERMINAL_CHUNK_CHECKPOINT_IDENTITY_INVALID",
        "COOPERATIVE_TERMINAL_CHUNK_CHECKPOINT_FINGERPRINT_INVALID",
        "COOPERATIVE_TERMINAL_CHUNK_CHECKPOINT_NOT_OBJECT",
        "COOPERATIVE_TERMINAL_CHUNK_CURSOR_OUT_OF_RANGE",
        "COOPERATIVE_TERMINAL_CHUNK_PHASE_INVALID",
        "COOPERATIVE_TERMINAL_CHUNK_PROCESSED_GAMES_INVALID",
        "COOPERATIVE_TERMINAL_CHUNK_PROCESSED_GAME_NOT_OBJECT",
        "COOPERATIVE_TERMINAL_CHUNK_PROCESSED_GAME_INVALID",
        "COOPERATIVE_TERMINAL_CHUNK_MANIFEST_AUTHORITY_INVALID",
        "COOPERATIVE_TERMINAL_CHUNK_MANIFEST_IDENTITY_INVALID",
        "COOPERATIVE_TERMINAL_CHUNK_AMBIGUOUS_MANIFEST_IDENTITY",
        "COOPERATIVE_TERMINAL_CHUNK_MANIFEST_TOO_LARGE",
        "COOPERATIVE_TERMINAL_CHUNK_MANIFEST_EVIDENCE_INVALID",
        "COOPERATIVE_TERMINAL_CHUNK_MANIFEST_AUTHORITY_EVIDENCE_MISMATCH",
        "COOPERATIVE_TERMINAL_CHUNK_ATOMIC_EVIDENCE_CONFLICT",
        "COOPERATIVE_TERMINAL_CHUNK_ATOMIC_READ_SET_OUT_OF_RANGE",
        "COOPERATIVE_TERMINAL_CHUNK_ATOMIC_COMPLETION_PROOF_INVALID",
        "COOPERATIVE_TERMINAL_CHUNK_VERIFICATION_STATE_INVALID",
        "COOPERATIVE_TERMINAL_CHUNK_PROCESSEDGAMECOUNT_MISMATCH",
        "COOPERATIVE_TERMINAL_CHUNK_TERMINALCOUNT_MISMATCH",
        "COOPERATIVE_TERMINAL_CHUNK_CANONICALCOUNT_MISMATCH",
        "COOPERATIVE_TERMINAL_CHUNK_NOPREDICTIONDATACOUNT_MISMATCH",
        "COOPERATIVE_TERMINAL_CHUNK_MISSEDLOCKVALIDPRELOCKQUARANTINECOUNT_MISMATCH",
        "COOPERATIVE_TERMINAL_CHUNK_RECONCILEDCOUNT_MISMATCH",
        "COOPERATIVE_TERMINAL_CHUNK_VERIFIEDGAMECOUNT_MISMATCH",
        "COOPERATIVE_TERMINAL_CHUNK_DURABLE_EVIDENCE_INVALID",
        "COOPERATIVE_TERMINAL_CHUNK_DURABLE_TARGET_INVALID",
        "COOPERATIVE_TERMINAL_CHUNK_OUTCOME_READBACK_INVALID",
        "DURABLE_VERIFICATION_MISMATCH",
        "OUTCOME_OBSERVATION_INVALID",
        "AMBIGUOUS_DUAL_TERMINAL_AUTHORITY",
        "AMBIGUOUS_DURABLE_TERMINAL_IDENTITY",
        "NONCANONICAL_TERMINAL_ALIAS_REQUIRES_REVIEW",
        "IMMUTABLE_LOCK_OUTCOME_AUTHORITY_INVALID",
        "IMMUTABLE_STAGE_AUTHORITY_INVALID",
        "IMMUTABLE_CANONICAL_READBACK_MISSING",
        "TERMINAL_OUTCOME_STATUS_INVALID",
        "NO_PREDICTION_TERMINAL_AUTHORITY_INVALID",
        "VALID_PRELOCK_QUARANTINE_TERMINAL_AUTHORITY_INVALID",
        "DURABLE_TERMINAL_MANIFEST_AUTHORITY_MISMATCH",
        "VALID_PRELOCK_QUARANTINE_PROOF_INVALID",
        "VALID_PRELOCK_QUARANTINE_IDENTITY_INVALID",
        "VALID_PRELOCK_QUARANTINE_SNAPSHOT_KEY_MISSING",
        "VALID_PRELOCK_QUARANTINE_SNAPSHOT_MISSING",
        "VALID_PRELOCK_QUARANTINE_SNAPSHOT_ROW_INVALID",
        "VALID_PRELOCK_QUARANTINE_SNAPSHOT_PROOF_MISMATCH",
        "VALID_PRELOCK_QUARANTINE_TIMESTAMP_INVALID",
        "VALID_PRELOCK_QUARANTINE_SOURCE_PULL_PROOF_MISMATCH",
        "VALID_PRELOCK_QUARANTINE_MANIFEST_AUTHORITY_MISSING",
        "VALID_PRELOCK_QUARANTINE_MANIFEST_AUTHORITY_INVALID",
        "COOPERATIVE_TERMINAL_QUARANTINE_SOURCE_MISMATCH",
        "REQUEST_IDENTITY_INVALID",
        "COOPERATIVE_TERMINAL_CHUNK_IDENTITY_ALIAS_LIMIT_EXCEEDED",
        "COOPERATIVE_TERMINAL_CHUNK_MANIFEST_AUTHORITY_READBACK_MISSING",
        "COOPERATIVE_TERMINAL_ATOMIC_EVIDENCE_INVALID",
        "COOPERATIVE_TERMINAL_ATOMIC_EVIDENCE_CONFLICT",
        "COOPERATIVE_TERMINAL_ATOMIC_TABLE_ROLE_INVALID",
        "COOPERATIVE_TERMINAL_ATOMIC_TABLE_NAME_MISSING",
        "COOPERATIVE_TERMINAL_ATOMIC_ITEM_MISSING",
        "COOPERATIVE_TERMINAL_ATOMIC_ITEM_MISMATCH",
        "COOPERATIVE_TERMINAL_ATOMIC_RESPONSE_COUNT_MISMATCH",
        "COOPERATIVE_TERMINAL_ATOMIC_READ_SET_OUT_OF_RANGE",
        "COOPERATIVE_TERMINAL_CANONICAL_EVIDENCE_MISSING",
        "COOPERATIVE_TERMINAL_CANONICAL_EVIDENCE_READBACK_MISSING",
        "COOPERATIVE_TERMINAL_DEPENDENCY_KEY_MISSING",
        "COOPERATIVE_TERMINAL_DEPENDENCY_READBACK_MISSING",
        "COOPERATIVE_TERMINAL_DEPENDENCY_SET_TOO_LARGE",
        "COOPERATIVE_TERMINAL_EVIDENCE_KEY_INVALID",
        "COOPERATIVE_TERMINAL_EVIDENCE_STATE_INVALID",
        "COOPERATIVE_TERMINAL_MANIFEST_DEPENDENCY_INVALID",
        "COOPERATIVE_TERMINAL_OUTCOME_EVIDENCE_MISSING",
        "COOPERATIVE_TERMINAL_QUARANTINE_CANDIDATE_MISMATCH",
        "COOPERATIVE_TERMINAL_QUARANTINE_EVIDENCE_MISSING",
        "COOPERATIVE_TERMINAL_CANDIDATE_ALIAS_LIMIT_EXCEEDED",
        "COOPERATIVE_TERMINAL_CANDIDATE_ALIAS_LIMIT_INVALID",
        "PREGAME_CANDIDATE_QUERY_LIMIT_INVALID",
        "PREGAME_CANDIDATE_QUERY_RESPONSE_INVALID",
        "PREGAME_CANDIDATE_QUERY_ITEMS_INVALID",
        "PREGAME_CANDIDATE_QUERY_LAST_EVALUATED_KEY_INVALID",
        "PREGAME_CANDIDATE_QUERY_BOUND_EXCEEDED",
        "COOPERATIVE_TERMINAL_CHUNK_ATOMIC_ITEM_COUNT_INVALID",
        "COOPERATIVE_TERMINAL_CHUNK_ATTEMPT_COUNT_INVALID",
        "COOPERATIVE_TERMINAL_CHUNK_AUTHORITY_GAME_COUNT_INVALID",
        "COOPERATIVE_TERMINAL_CHUNK_AUTHORITY_ITEM_COUNT_INVALID",
        "COOPERATIVE_TERMINAL_CHUNK_CANDIDATE_ALIAS_QUERY_LIMIT_INVALID",
        "COOPERATIVE_TERMINAL_CHUNK_CANONICAL_COUNT_INVALID",
        "COOPERATIVE_TERMINAL_CHUNK_CHECKPOINT_REQUEST_EPOCH_INVALID",
        "COOPERATIVE_TERMINAL_CHUNK_DEPENDENCY_ITEM_COUNT_INVALID",
        "COOPERATIVE_TERMINAL_CHUNK_IDENTITY_ALIAS_LIMIT_INVALID",
        "COOPERATIVE_TERMINAL_CHUNK_MANIFEST_GAME_COUNT_INVALID",
        "COOPERATIVE_TERMINAL_CHUNK_MISSED_LOCK_VALID_PRELOCK_QUARANTINE_COUNT_INVALID",
        "COOPERATIVE_TERMINAL_CHUNK_NEXT_GAME_INDEX_INVALID",
        "COOPERATIVE_TERMINAL_CHUNK_NO_PREDICTION_DATA_COUNT_INVALID",
        "COOPERATIVE_TERMINAL_CHUNK_PROCESSED_GAME_COUNT_INVALID",
        "COOPERATIVE_TERMINAL_CHUNK_PROOF_ITEM_COUNT_INVALID",
        "COOPERATIVE_TERMINAL_CHUNK_PROOF_REQUEST_EPOCH_INVALID",
        "COOPERATIVE_TERMINAL_CHUNK_PROOF_VERIFIED_AT_EPOCH_INVALID",
        "COOPERATIVE_TERMINAL_CHUNK_QUARANTINE_BOUND_SCORING_PULL_COUNT_INVALID",
        "COOPERATIVE_TERMINAL_CHUNK_QUARANTINE_REJECTED_NEWER_CANDIDATE_COUNT_INVALID",
        "COOPERATIVE_TERMINAL_CHUNK_RECONCILED_COUNT_INVALID",
        "COOPERATIVE_TERMINAL_CHUNK_REQUEST_EPOCH_INVALID",
        "COOPERATIVE_TERMINAL_CHUNK_SCHEDULE_AUTHORITY_GAME_COUNT_INVALID",
        "COOPERATIVE_TERMINAL_CHUNK_TERMINAL_COUNT_INVALID",
        "COOPERATIVE_TERMINAL_CHUNK_V3_CANONICAL_COUNT_INVALID",
        "COOPERATIVE_TERMINAL_CHUNK_V3_LAST_ATTEMPT_GAME_INDEX_INVALID",
        "COOPERATIVE_TERMINAL_CHUNK_V3_LAST_ATTEMPT_AT_UTC_INVALID",
        "COOPERATIVE_TERMINAL_CHUNK_V3_UPDATED_AT_UTC_INVALID",
        "COOPERATIVE_TERMINAL_CHUNK_V3_NEXT_GAME_INDEX_INVALID",
        "COOPERATIVE_TERMINAL_CHUNK_V3_NO_PREDICTION_DATA_COUNT_INVALID",
        "COOPERATIVE_TERMINAL_CHUNK_V3_PROCESSED_GAME_COUNT_INVALID",
        "COOPERATIVE_TERMINAL_CHUNK_V3_RECONCILED_COUNT_INVALID",
        "COOPERATIVE_TERMINAL_CHUNK_V3_TERMINAL_COUNT_INVALID",
        "COOPERATIVE_TERMINAL_CHUNK_V3_VERIFICATION_INDEX_INVALID",
        "COOPERATIVE_TERMINAL_CHUNK_V3_VERIFIED_GAME_COUNT_INVALID",
        "COOPERATIVE_TERMINAL_CHUNK_VERIFICATION_INDEX_INVALID",
        "COOPERATIVE_TERMINAL_CHUNK_VERIFIED_GAME_COUNT_INVALID",
    }
)
COOPERATIVE_TERMINAL_COMPLETION_HANDOFF_VERSION = (
    "MLB-COOPERATIVE-TERMINAL-COMPLETION-HANDOFF-v1"
)
COOPERATIVE_TERMINAL_ATOMIC_MAX_ITEMS = 100
COOPERATIVE_REPLAY_MIN_REMAINING_SECONDS = (
    COOPERATIVE_REPLAY_EXECUTION_BUDGET_SECONDS
    + LOCK_EXECUTION_TIMEOUT_SAFETY_MARGIN_SECONDS
)
COOPERATIVE_CURRENT_SLATE_PROOF_VERSION = (
    "MLB-COOPERATIVE-CURRENT-SLATE-PROOF-v1-request-bound"
)
# EventBridge invokes once per minute and drops events older than one minute.
# Three schedule periods admit normal delivery jitter while ensuring a replay
# can never rely on a stale current-slate success.
COOPERATIVE_CURRENT_SLATE_PROOF_MAX_AGE_SECONDS = 180
try:
    LOCK_EXECUTION_LEASE_SECONDS = int(
        os.environ.get("MLB_LOCK_EXECUTION_LEASE_SECONDS", "960")
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
    and _lock_execution_lease_seconds == 960
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
        "lambdaTimeoutSeconds": 900,
        "timeoutSafetyMarginSeconds": (
            LOCK_EXECUTION_TIMEOUT_SAFETY_MARGIN_SECONDS
        ),
        "expiredLeaseReclaim": True,
        "ownerConditionalRelease": True,
        "reservedLambdaConcurrencyRequired": False,
    },
    "cooperativeTerminalReplayHandoff": {
        "version": COOPERATIVE_TERMINAL_REPLAY_VERSION,
        "queueScope": "one_exact_historical_mlb_slate",
        "automaticExecutionOwner": "eventbridge_daily_lock_schedule",
        "currentSlateRunsFirst": True,
        "freshPriorOwnerProofMayCarryAcrossInvocation": True,
        "currentSlateProofVersion": COOPERATIVE_CURRENT_SLATE_PROOF_VERSION,
        "currentSlateProofMaxAgeSeconds": (
            COOPERATIVE_CURRENT_SLATE_PROOF_MAX_AGE_SECONDS
        ),
        "minimumRemainingSeconds": COOPERATIVE_REPLAY_MIN_REMAINING_SECONDS,
        "activeLeaseMutationAllowed": False,
        "postStartPredictionCreationAllowed": False,
        "immutablePredictionRewriteAllowed": False,
        "failClosed": True,
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


def _cooperative_replay_key() -> Dict[str, str]:
    # This is deliberately a different sort key from the execution lease.  No
    # queue transition can delete, replace, extend, or otherwise mutate the
    # active owner lease.
    return {
        "PK": LOCK_EXECUTION_LEASE_PK,
        "SK": COOPERATIVE_TERMINAL_REPLAY_SK,
    }


def _cooperative_replay_table() -> Any:
    table = getattr(mlb_daily_pick_lock, "TABLE", None)
    if table is None:
        raise RuntimeError("MLB_COOPERATIVE_REPLAY_TABLE_NOT_CONFIGURED")
    return table


def _strict_historical_slate_date(event: Dict[str, Any]) -> str:
    if str(event.get("sport") or "") != "mlb":
        raise RuntimeError("MLB_COOPERATIVE_REPLAY_SPORT_INVALID")
    if str(event.get("run") or "") != COOPERATIVE_TERMINAL_REPLAY_RUN:
        raise RuntimeError("MLB_COOPERATIVE_REPLAY_RUN_INVALID")
    if event.get("force") is not True:
        raise RuntimeError("MLB_COOPERATIVE_REPLAY_FORCE_PROOF_MISSING")
    slate_date = str(event.get("slateDateEt") or "").strip()
    try:
        parsed = datetime.strptime(slate_date, "%Y-%m-%d").date()
    except (TypeError, ValueError) as exc:
        raise RuntimeError("MLB_COOPERATIVE_REPLAY_SLATE_DATE_INVALID") from exc
    if parsed.isoformat() != slate_date:
        raise RuntimeError("MLB_COOPERATIVE_REPLAY_SLATE_DATE_INVALID")
    today_reader = getattr(mlb_daily_pick_lock, "_today_et", None)
    if not callable(today_reader):
        raise RuntimeError("MLB_COOPERATIVE_REPLAY_TODAY_AUTHORITY_MISSING")
    try:
        today_et = datetime.strptime(str(today_reader()), "%Y-%m-%d").date()
    except (TypeError, ValueError) as exc:
        raise RuntimeError("MLB_COOPERATIVE_REPLAY_TODAY_AUTHORITY_INVALID") from exc
    if parsed >= today_et:
        raise RuntimeError("MLB_COOPERATIVE_REPLAY_NOT_EXACT_HISTORICAL_DATE")
    return slate_date


def _is_cooperative_replay_request(
    event: Dict[str, Any], method: Optional[str]
) -> bool:
    return bool(
        method is None
        and _is_scheduled(event)
        and str(event.get("run") or "") == COOPERATIVE_TERMINAL_REPLAY_RUN
    )


def _cooperative_record(item: Any, slate_date: str) -> Dict[str, Any]:
    if not isinstance(item, dict):
        raise RuntimeError("MLB_COOPERATIVE_REPLAY_RECORD_INVALID")
    if (
        item.get("PK") != LOCK_EXECUTION_LEASE_PK
        or item.get("SK") != COOPERATIVE_TERMINAL_REPLAY_SK
        or item.get("record_type") != COOPERATIVE_TERMINAL_REPLAY_RECORD_TYPE
        or item.get("coordination_version")
        != COOPERATIVE_TERMINAL_REPLAY_VERSION
    ):
        raise RuntimeError("MLB_COOPERATIVE_REPLAY_RECORD_IDENTITY_INVALID")
    if str(item.get("slate_date_et") or "") != slate_date:
        raise RuntimeError("MLB_COOPERATIVE_REPLAY_DIFFERENT_REQUEST_ACTIVE")
    raw_request_epoch = item.get("requested_at_epoch")
    if isinstance(raw_request_epoch, bool):
        raise RuntimeError(
            "MLB_COOPERATIVE_REPLAY_REQUEST_EPOCH_INVALID"
        )
    try:
        request_epoch_decimal = Decimal(str(raw_request_epoch))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise RuntimeError(
            "MLB_COOPERATIVE_REPLAY_REQUEST_EPOCH_INVALID"
        ) from exc
    if (
        not request_epoch_decimal.is_finite()
        or request_epoch_decimal <= 0
        or request_epoch_decimal
        != request_epoch_decimal.to_integral_value()
    ):
        raise RuntimeError(
            "MLB_COOPERATIVE_REPLAY_REQUEST_EPOCH_INVALID"
        )
    request_epoch = int(request_epoch_decimal)
    normalized = dict(item)
    if not str(normalized.get("request_id") or "").strip():
        # One-time rollout bridge for a pre-v2 coordination row. The derived
        # ID is request-specific and is conditionally persisted by the next
        # proof/claim update before any chunk can checkpoint or complete.
        normalized["request_id"] = "legacy-" + uuid.uuid5(
            uuid.NAMESPACE_URL,
            (
                f"mlb-cooperative-replay:{slate_date}:"
                f"{request_epoch}:"
                f"{normalized.get('requested_at_utc') or ''}"
            ),
        ).hex
    item = normalized
    state = str(item.get("state") or "")
    if state not in {
        COOPERATIVE_REPLAY_QUEUED,
        COOPERATIVE_REPLAY_CLAIMED,
        COOPERATIVE_REPLAY_COMPLETED,
        COOPERATIVE_REPLAY_ACKNOWLEDGED,
        COOPERATIVE_REPLAY_REVIEW_REQUIRED,
    }:
        raise RuntimeError("MLB_COOPERATIVE_REPLAY_STATE_INVALID")
    return dict(item)



def _cooperative_terminal_progress_public(
    item: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    progress = item.get("terminal_replay_progress")
    if not isinstance(progress, dict):
        return None

    def safe_integer(field: str) -> Optional[int]:
        value = progress.get(field)
        if isinstance(value, bool):
            return None
        try:
            numeric = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return None
        if (
            not numeric.is_finite()
            or numeric < 0
            or numeric != numeric.to_integral_value()
        ):
            return None
        return int(numeric)

    fields = (
        "manifestGameCount",
        "nextGameIndex",
        "processedGameCount",
        "terminalCount",
        "canonicalCount",
        "noPredictionDataCount",
        "missedLockValidPrelockQuarantineCount",
        "reconciledCount",
        "verificationIndex",
        "verifiedGameCount",
        "attemptCount",
    )
    values = {field: safe_integer(field) for field in fields}
    if (
        progress.get("version") != COOPERATIVE_TERMINAL_CHUNK_VERSION
        or str(progress.get("slateDateEt") or "")
        != str(item.get("slate_date_et") or "")
        or any(value is None for value in values.values())
        or str(progress.get("phase") or "") not in {"PROCESS", "VERIFY"}
        or not isinstance(progress.get("verificationComplete"), bool)
        or progress.get("postStartPredictionCreationAllowed") is not False
        or progress.get("immutablePredictionRewriteAllowed") is not False
        or progress.get("productionAuthorityChanged") is not False
    ):
        return {
            "version": COOPERATIVE_TERMINAL_CHUNK_VERSION,
            "valid": False,
            "failClosed": True,
        }
    manifest_count = int(values["manifestGameCount"] or 0)
    next_index = int(values["nextGameIndex"] or 0)
    public = {
        "version": COOPERATIVE_TERMINAL_CHUNK_VERSION,
        "valid": True,
        **values,
        "phase": str(progress.get("phase") or ""),
        "verificationComplete": progress.get("verificationComplete"),
        "remainingGameCount": max(manifest_count - next_index, 0),
        "remainingVerificationCount": max(
            manifest_count - int(values["verificationIndex"] or 0),
            0,
        ),
        "oneGamePerEventBridgeOwner": True,
        "postStartPredictionCreationAllowed": False,
        "immutablePredictionRewriteAllowed": False,
        "productionAuthorityChanged": False,
    }
    if progress.get("verificationComplete") is True:
        try:
            terminal_games, terminal_game_set_fingerprint = (
                _validated_terminal_game_receipt(
                    progress,
                    manifest_count=manifest_count,
                    canonical_count=int(values["canonicalCount"] or 0),
                    no_prediction_count=int(
                        values["noPredictionDataCount"] or 0
                    ),
                    quarantine_count=int(
                        values[
                            "missedLockValidPrelockQuarantineCount"
                        ]
                        or 0
                    ),
                )
            )
        except RuntimeError:
            return {
                "version": COOPERATIVE_TERMINAL_CHUNK_VERSION,
                "valid": False,
                "failClosed": True,
            }
        fingerprint_fields = (
            "manifestFingerprint",
            "checkpointFingerprint",
            "manifestAuthorityEvidenceFingerprint",
            "providerManifestFingerprint",
            "atomicDurableReadSetFingerprint",
        )
        if any(
            re.fullmatch(
                r"[0-9a-f]{64}",
                str(progress.get(field) or ""),
            )
            is None
            for field in fingerprint_fields
        ):
            return {
                "version": COOPERATIVE_TERMINAL_CHUNK_VERSION,
                "valid": False,
                "failClosed": True,
            }
        public.update(
            {
                field: str(progress.get(field) or "")
                for field in fingerprint_fields
            }
        )
        public["terminalGames"] = terminal_games
        public["terminalGameSetFingerprint"] = (
            terminal_game_set_fingerprint
        )
    last_attempt = progress.get("lastAttempt")
    if isinstance(last_attempt, dict):
        public["lastAttempt"] = {
            key: last_attempt.get(key)
            for key in (
                "status",
                "stage",
                "atUtc",
                "gameIndex",
                "gameIdentity",
                "durableIdentity",
                "phase",
                "errorCode",
            )
            if last_attempt.get(key) is not None
        }
    return public

def _cooperative_review_reason(item: Dict[str, Any]) -> str:
    progress = item.get("terminal_replay_progress")
    attempt = (
        progress.get("lastAttempt")
        if isinstance(progress, dict)
        else None
    )
    code = (
        str(attempt.get("errorCode") or "")
        if isinstance(attempt, dict)
        else ""
    )
    progress_is_bound = bool(
        isinstance(progress, dict)
        and str(progress.get("slateDateEt") or "")
        == str(item.get("slate_date_et") or "")
        and (
            progress.get("requestEpoch") is None
            or progress.get("requestEpoch")
            == item.get("requested_at_epoch")
        )
        and (
            not str(progress.get("requestId") or "")
            or str(progress.get("requestId") or "")
            == str(item.get("request_id") or "")
        )
    )
    safe_code = bool(
        code in COOPERATIVE_REPLAY_PERMANENT_REVIEW_ERRORS
        and len(code) <= 160
        and all(
            character.isupper()
            or character.isdigit()
            or character in "_:-."
            for character in code
        )
    )
    return (
        code
        if progress_is_bound and safe_code
        else "PRELOCK_CANDIDATE_REQUIRES_REVIEW"
    )


def _cooperative_public_state(item: Dict[str, Any]) -> Dict[str, Any]:
    state = str(item.get("state") or "")
    public = {
        "version": COOPERATIVE_TERMINAL_REPLAY_VERSION,
        "state": state,
        "slateDateEt": str(item.get("slate_date_et") or ""),
        "automaticExecutionOwner": "eventbridge_daily_lock_schedule",
        "currentSlateRunsFirst": True,
        "freshPriorOwnerProofMayCarryAcrossInvocation": True,
        "currentSlateSuccessProofPresent": isinstance(
            item.get("current_slate_success_proof"), dict
        ),
        "activeLeaseMutationAllowed": False,
        "postStartPredictionCreationAllowed": False,
        "immutablePredictionRewriteAllowed": False,
        "directWorkflowTableWrite": False,
        "ownerIdentifierExposed": False,
        "terminalChunkProgress": _cooperative_terminal_progress_public(item),
        "failClosed": True,
    }
    if state == COOPERATIVE_REPLAY_REVIEW_REQUIRED:
        public["reviewRequired"] = True
        public["reviewReason"] = _cooperative_review_reason(item)
        public["staleClaimReclaimable"] = False
    return public



def _validated_persisted_replay_receipt(
    item: Dict[str, Any],
) -> Dict[str, Any]:
    slate_date = str(item.get("slate_date_et") or "")
    raw_receipt = item.get("replay_receipt")
    if not isinstance(raw_receipt, dict):
        raise RuntimeError("MLB_COOPERATIVE_REPLAY_RECEIPT_MISSING")
    receipt = _terminal_replay_receipt(raw_receipt, slate_date)
    if receipt != raw_receipt:
        raise RuntimeError(
            "MLB_COOPERATIVE_REPLAY_PERSISTED_RECEIPT_INVALID"
        )
    expected_progress = item.get("terminal_replay_progress")
    validator = getattr(
        mlb_daily_pick_lock,
        "validate_cooperative_terminal_completion_checkpoint",
        None,
    )
    if not isinstance(expected_progress, dict) or not callable(validator):
        raise RuntimeError(
            "MLB_COOPERATIVE_REPLAY_PERSISTED_RECEIPT_INVALID"
        )
    try:
        validated = validator(expected_progress)
    except BaseException as exc:
        raise RuntimeError(
            "MLB_COOPERATIVE_REPLAY_PERSISTED_RECEIPT_INVALID"
        ) from exc
    if (
        not isinstance(validated, tuple)
        or len(validated) != 3
        or validated[0] != expected_progress
        or not isinstance(validated[1], list)
        or re.fullmatch(r"[0-9a-f]{64}", str(validated[2] or ""))
        is None
    ):
        raise RuntimeError(
            "MLB_COOPERATIVE_REPLAY_PERSISTED_RECEIPT_INVALID"
        )
    expected_games, expected_game_fingerprint = (
        _checkpoint_terminal_game_receipt(expected_progress)
    )
    progress = receipt["perGameLockProgress"]
    authority = expected_progress.get("manifestAuthority")
    if (
        not isinstance(authority, Mapping)
        or receipt.get("checkpointFingerprint")
        != expected_progress.get("checkpointFingerprint")
        or receipt.get("manifestFingerprint")
        != expected_progress.get("manifestFingerprint")
        or progress.get("terminalGames") != expected_games
        or progress.get("terminalGameSetFingerprint")
        != expected_game_fingerprint
        or progress.get("manifestAuthorityEvidenceFingerprint")
        != authority.get("authorityEvidenceFingerprint")
        or progress.get("providerManifestFingerprint")
        != authority.get("fingerprint")
        or _nonnegative_receipt_integer(
            progress.get("atomicDurableItemCount"),
            "persisted_receipt_atomic_durable_item_count",
        )
        != len(validated[1])
        or progress.get("atomicDurableReadSetFingerprint")
        != validated[2]
    ):
        raise RuntimeError(
            "MLB_COOPERATIVE_REPLAY_PERSISTED_RECEIPT_INVALID"
        )
    return receipt

def _cooperative_request_response(item: Dict[str, Any]) -> Dict[str, Any]:
    state = str(item.get("state") or "")
    public = _cooperative_public_state(item)
    if state in {
        COOPERATIVE_REPLAY_COMPLETED,
        COOPERATIVE_REPLAY_ACKNOWLEDGED,
    }:
        receipt = _validated_persisted_replay_receipt(item)
        result = dict(receipt)
        result["cooperativeTerminalReplay"] = public
        result["cooperativeTerminalReplayCompleted"] = True
        result["mutatingRunAttemptedByPollingRequest"] = False
        return result
    if state == COOPERATIVE_REPLAY_REVIEW_REQUIRED:
        return {
            "ok": False,
            "sport": "mlb",
            "slateDateEt": str(item.get("slate_date_et") or ""),
            "status": COOPERATIVE_REPLAY_REVIEW_REQUIRED,
            "reason": _cooperative_review_reason(item),
            "reviewRequired": True,
            "skipped": True,
            "mutatingRunAttempted": False,
            "cooperativeTerminalReplayCompleted": False,
            "cooperativeTerminalReplay": public,
            "activeLeaseMutationAllowed": False,
            "postStartPredictionCreationAllowed": False,
            "immutablePredictionRewriteAllowed": False,
            "directWorkflowTableWrite": False,
            "productionAuthorityChanged": False,
        }
    return {
        "ok": True,
        "sport": "mlb",
        "slateDateEt": str(item.get("slate_date_et") or ""),
        "status": "QUEUED_FOR_EVENTBRIDGE_LOCK_OWNER",
        "reason": "QUEUED_FOR_EVENTBRIDGE_LOCK_OWNER",
        "skipped": True,
        "mutatingRunAttempted": False,
        "cooperativeTerminalReplayCompleted": False,
        "cooperativeTerminalReplay": public,
        "activeLeaseMutationAllowed": False,
        "postStartPredictionCreationAllowed": False,
        "immutablePredictionRewriteAllowed": False,
        "directWorkflowTableWrite": False,
        "productionAuthorityChanged": False,
    }


def _read_cooperative_replay() -> Dict[str, Any]:
    table = _cooperative_replay_table()
    return dict(
        table.get_item(
            Key=_cooperative_replay_key(),
            ConsistentRead=True,
        ).get("Item")
        or {}
    )


def _enqueue_or_read_cooperative_replay(event: Dict[str, Any]) -> Dict[str, Any]:
    slate_date = _strict_historical_slate_date(event)
    now = _utc_now()
    item = {
        **_cooperative_replay_key(),
        "record_type": COOPERATIVE_TERMINAL_REPLAY_RECORD_TYPE,
        "coordination_version": COOPERATIVE_TERMINAL_REPLAY_VERSION,
        "state": COOPERATIVE_REPLAY_QUEUED,
        "sport": "mlb",
        "run": COOPERATIVE_TERMINAL_REPLAY_RUN,
        "slate_date_et": slate_date,
        "force": True,
        "requested_at_utc": now.isoformat(),
        "requested_at_epoch": int(now.timestamp()),
        "request_id": uuid.uuid4().hex,
        "post_start_prediction_creation_allowed": False,
        "immutable_prediction_rewrite_allowed": False,
        "active_lease_mutation_allowed": False,
        "direct_workflow_table_write": False,
    }
    existing = _read_cooperative_replay()
    replace_acknowledged_date: Optional[str] = None
    if existing:
        existing_date = str(existing.get("slate_date_et") or "")
        validated = _cooperative_record(existing, existing_date)
        if existing_date == slate_date:
            # Returning a completed receipt is itself the durable observation
            # made by legacy v5.7.  Auto-acknowledge before returning while
            # retaining the receipt, so an old checkout can advance to its
            # next exact slate without any direct DynamoDB operation.  A lost
            # response remains idempotent because ACKNOWLEDGED stores and
            # returns the same redacted receipt.
            if validated.get("state") == COOPERATIVE_REPLAY_COMPLETED:
                _acknowledge_cooperative_replay(
                    event,
                    expected_completed=validated,
                )
                # The coordination slot may already have been conditionally
                # replaced by the next exact date after ACK committed.  Keep
                # the original durable receipt and project only its state;
                # rereading the mutable slot here could hide that receipt.
                validated = dict(validated)
                validated["state"] = COOPERATIVE_REPLAY_ACKNOWLEDGED
            return _cooperative_request_response(validated)
        if validated.get("state") != COOPERATIVE_REPLAY_ACKNOWLEDGED:
            raise RuntimeError("MLB_COOPERATIVE_REPLAY_DIFFERENT_REQUEST_ACTIVE")
        replace_acknowledged_date = existing_date

    table = _cooperative_replay_table()
    try:
        if replace_acknowledged_date is None:
            table.put_item(
                Item=item,
                ConditionExpression=(
                    "attribute_not_exists(PK) AND attribute_not_exists(SK)"
                ),
            )
        else:
            table.put_item(
                Item=item,
                ConditionExpression=(
                    "record_type = :record_type AND "
                    "coordination_version = :version AND "
                    "slate_date_et = :previous_slate_date AND "
                    "#state = :acknowledged"
                ),
                ExpressionAttributeNames={"#state": "state"},
                ExpressionAttributeValues={
                    ":record_type": COOPERATIVE_TERMINAL_REPLAY_RECORD_TYPE,
                    ":version": COOPERATIVE_TERMINAL_REPLAY_VERSION,
                    ":previous_slate_date": replace_acknowledged_date,
                    ":acknowledged": COOPERATIVE_REPLAY_ACKNOWLEDGED,
                },
            )
        return _cooperative_request_response(item)
    except BaseException as exc:
        # Both a normal race and an ambiguous write are resolved by one strong
        # read.  Only the exact same request is accepted idempotently.
        observed = _read_cooperative_replay()
        if observed:
            try:
                validated = _cooperative_record(observed, slate_date)
                if validated.get("state") == COOPERATIVE_REPLAY_COMPLETED:
                    _acknowledge_cooperative_replay(
                        event,
                        expected_completed=validated,
                    )
                    validated = dict(validated)
                    validated["state"] = COOPERATIVE_REPLAY_ACKNOWLEDGED
                return _cooperative_request_response(validated)
            except RuntimeError:
                raise
        if _error_code(exc) == "ConditionalCheckFailedException":
            raise RuntimeError("MLB_COOPERATIVE_REPLAY_QUEUE_CONFLICT") from exc
        raise


def _acknowledge_cooperative_replay(
    event: Dict[str, Any],
    *,
    expected_completed: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    slate_date = _strict_historical_slate_date(event)
    item = _cooperative_record(
        expected_completed
        if expected_completed is not None
        else _read_cooperative_replay(),
        slate_date,
    )
    if item.get("state") == COOPERATIVE_REPLAY_ACKNOWLEDGED:
        return {
            "ok": True,
            "sport": "mlb",
            "slateDateEt": slate_date,
            "cooperativeTerminalReplayAcknowledged": True,
            "cooperativeTerminalReplay": _cooperative_public_state(item),
        }
    if item.get("state") != COOPERATIVE_REPLAY_COMPLETED:
        raise RuntimeError("MLB_COOPERATIVE_REPLAY_NOT_COMPLETE")
    now = _utc_now()
    table = _cooperative_replay_table()
    try:
        updated = table.update_item(
            Key=_cooperative_replay_key(),
            ConditionExpression=(
                "record_type = :record_type AND "
                "coordination_version = :version AND "
                "slate_date_et = :slate_date AND #state = :completed"
            ),
            UpdateExpression=(
                "SET #state = :acknowledged, acknowledged_at_utc = :now_utc, "
                "acknowledged_at_epoch = :now_epoch"
            ),
            ExpressionAttributeNames={"#state": "state"},
            ExpressionAttributeValues={
                ":record_type": COOPERATIVE_TERMINAL_REPLAY_RECORD_TYPE,
                ":version": COOPERATIVE_TERMINAL_REPLAY_VERSION,
                ":slate_date": slate_date,
                ":completed": COOPERATIVE_REPLAY_COMPLETED,
                ":acknowledged": COOPERATIVE_REPLAY_ACKNOWLEDGED,
                ":now_utc": now.isoformat(),
                ":now_epoch": int(now.timestamp()),
            },
            ReturnValues="ALL_NEW",
        ).get("Attributes")
    except BaseException as exc:
        observed_raw = _read_cooperative_replay()
        observed_date = str(observed_raw.get("slate_date_et") or "")
        if observed_date == slate_date:
            observed = _cooperative_record(observed_raw, slate_date)
            if observed.get("state") != COOPERATIVE_REPLAY_ACKNOWLEDGED:
                raise RuntimeError(
                    "MLB_COOPERATIVE_REPLAY_ACKNOWLEDGE_FAILED"
                ) from exc
            updated = observed
        else:
            # Replacing this single slot with a different request is allowed
            # only by a conditional put whose prior state was ACKNOWLEDGED.
            # A valid replacement therefore proves this acknowledgement
            # committed even if the update response was ambiguous and the
            # replacement won the subsequent strong-read race.
            replacement = _cooperative_record(observed_raw, observed_date)
            _strict_historical_slate_date(
                {
                    "sport": "mlb",
                    "run": COOPERATIVE_TERMINAL_REPLAY_RUN,
                    "slateDateEt": str(replacement.get("slate_date_et") or ""),
                    "force": True,
                }
            )
            updated = dict(item)
            updated["state"] = COOPERATIVE_REPLAY_ACKNOWLEDGED
    return {
        "ok": True,
        "sport": "mlb",
        "slateDateEt": slate_date,
        "cooperativeTerminalReplayAcknowledged": True,
        "cooperativeTerminalReplay": _cooperative_public_state(
            dict(updated or item)
        ),
    }


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
        # 960-second lease below the Lambda timeout plus its 60-second margin.
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


def _cooperative_replay_requires_review(
    stage: Any,
    error_code: Any,
) -> bool:
    """Classify only deterministic persisted-authority defects.

    Budget, lease, SDK, network, and generic runtime failures deliberately stay
    retryable.  This avoids both a hot loop on immutable corrupt evidence and
    an over-broad quarantine of transient infrastructure faults.
    """

    stage_value = str(stage or "")
    code_value = str(error_code or "")
    if code_value not in COOPERATIVE_REPLAY_PERMANENT_REVIEW_ERRORS:
        return False
    if code_value == "PRELOCK_CANDIDATE_REQUIRES_REVIEW":
        return stage_value == "PROVE_PRELOCK_ABSENCE"
    if code_value == "COOPERATIVE_TERMINAL_CHUNK_V3_MIGRATION_NOT_ZERO_WORK":
        return stage_value == "BIND_MANIFEST_AUTHORITY"
    return stage_value in {
        "BIND_REQUEST",
        "RESOLVE_MANIFEST",
        "BIND_MANIFEST_AUTHORITY",
        "READ_DURABLE_TERMINAL",
        "VERIFY_DURABLE_TERMINAL",
        "PROVE_PRELOCK_ABSENCE",
        "WRITE_VALID_PRELOCK_MISSED_LOCK_QUARANTINE",
        "READBACK_VALID_PRELOCK_QUARANTINE_TERMINAL",
        "READBACK_NO_PREDICTION_TERMINAL",
        "ATOMIC_COMPLETION_PROOF",
    }


def _remaining_seconds(context: Any) -> int:
    reader = getattr(context, "get_remaining_time_in_millis", None)
    if not callable(reader):
        return 0
    try:
        return max(0, int(reader()) // 1000)
    except (TypeError, ValueError):
        return 0


def _is_eventbridge_daily_lock_owner(event: Dict[str, Any]) -> bool:
    return bool(
        _is_scheduled(event)
        and str(event.get("sport") or "") == "mlb"
        and str(event.get("run") or "") == "daily_lock_check"
        and event.get("auto_ingest") is False
        and event.get("force") not in {True, "true", "1", 1}
    )


def _current_slate_success_proof_is_fresh(
    item: Dict[str, Any],
    *,
    expected_current_slate_date: str,
) -> bool:
    proof = item.get("current_slate_success_proof")
    if not isinstance(proof, dict):
        return False
    try:
        request_epoch = int(item.get("requested_at_epoch"))
        proof_request_epoch = int(proof.get("requestEpoch"))
        proof_request_id = str(proof.get("requestId") or "")
        processed_epoch = int(proof.get("processedAtEpoch"))
    except (TypeError, ValueError):
        return False
    now_epoch = int(_utc_now().timestamp())
    age_seconds = now_epoch - processed_epoch
    return bool(
        proof.get("version") == COOPERATIVE_CURRENT_SLATE_PROOF_VERSION
        and str(proof.get("currentSlateDateEt") or "")
        == expected_current_slate_date
        and str(proof.get("requestSlateDateEt") or "")
        == str(item.get("slate_date_et") or "")
        and proof_request_epoch == request_epoch
        and proof_request_id == str(item.get("request_id") or "")
        and processed_epoch >= request_epoch
        and 0 <= age_seconds
        <= COOPERATIVE_CURRENT_SLATE_PROOF_MAX_AGE_SECONDS
        and proof.get("postStartPredictionCreationAllowed") is False
        and proof.get("immutablePredictionRewriteAllowed") is False
    )


def _persist_current_slate_success_proof(
    *,
    current_slate_response: Any,
    expected_current_slate_date: str,
    remaining_seconds: int,
) -> Dict[str, Any]:
    item = _read_cooperative_replay()
    if not item:
        return {
            "version": COOPERATIVE_TERMINAL_REPLAY_VERSION,
            "state": "NO_PENDING_REQUEST",
            "currentSlateRanFirst": True,
            "remainingSeconds": remaining_seconds,
            "queueReadAttempted": True,
            "activeLeaseMutationAllowed": False,
        }
    historical_slate_date = str(item.get("slate_date_et") or "")
    item = _cooperative_record(item, historical_slate_date)
    state = str(item.get("state") or "")
    if state in {
        COOPERATIVE_REPLAY_COMPLETED,
        COOPERATIVE_REPLAY_ACKNOWLEDGED,
    }:
        return {
            **_cooperative_public_state(item),
            "currentSlateRanFirst": True,
            "remainingSeconds": remaining_seconds,
            "queueReadAttempted": True,
        }
    _assert_current_slate_processed_before_handoff(
        current_slate_response,
        expected_slate_date=expected_current_slate_date,
    )

    now = _utc_now()
    now_epoch = int(now.timestamp())
    try:
        request_epoch = int(item.get("requested_at_epoch"))
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            "MLB_COOPERATIVE_REPLAY_REQUEST_EPOCH_INVALID"
        ) from exc
    if request_epoch > now_epoch:
        raise RuntimeError("MLB_COOPERATIVE_REPLAY_REQUEST_EPOCH_IN_FUTURE")
    proof = {
        "version": COOPERATIVE_CURRENT_SLATE_PROOF_VERSION,
        "currentSlateDateEt": expected_current_slate_date,
        "requestSlateDateEt": historical_slate_date,
        "requestEpoch": request_epoch,
        "requestId": str(item.get("request_id") or ""),
        "processedAtEpoch": now_epoch,
        "postStartPredictionCreationAllowed": False,
        "immutablePredictionRewriteAllowed": False,
    }
    table = _cooperative_replay_table()
    try:
        updated = table.update_item(
            Key=_cooperative_replay_key(),
            ConditionExpression=(
                "record_type = :record_type AND "
                "coordination_version = :version AND "
                "slate_date_et = :slate_date AND "
                "requested_at_epoch = :request_epoch AND "
                "(request_id = :request_id OR attribute_not_exists(request_id)) AND "
                "(#state = :queued OR "
                "(#state = :claimed AND claim_expires_at_epoch <= :now_epoch))"
            ),
            UpdateExpression=(
                "SET request_id = :request_id, "
                "current_slate_success_proof = :proof"
            ),
            ExpressionAttributeNames={"#state": "state"},
            ExpressionAttributeValues={
                ":record_type": COOPERATIVE_TERMINAL_REPLAY_RECORD_TYPE,
                ":version": COOPERATIVE_TERMINAL_REPLAY_VERSION,
                ":slate_date": historical_slate_date,
                ":request_epoch": request_epoch,
                ":request_id": str(item.get("request_id") or ""),
                ":queued": COOPERATIVE_REPLAY_QUEUED,
                ":claimed": COOPERATIVE_REPLAY_CLAIMED,
                ":now_epoch": now_epoch,
                ":proof": proof,
            },
            ReturnValues="ALL_NEW",
        ).get("Attributes")
    except BaseException as exc:
        observed = _cooperative_record(
            _read_cooperative_replay(),
            historical_slate_date,
        )
        if observed.get("current_slate_success_proof") == proof:
            updated = observed
        elif _error_code(exc) == "ConditionalCheckFailedException":
            return {
                **_cooperative_public_state(observed),
                "currentSlateRanFirst": True,
                "remainingSeconds": remaining_seconds,
                "queueReadAttempted": True,
                "currentSlateSuccessProofPersisted": False,
                "proofWriteLostToStateTransition": True,
            }
        else:
            raise
    proven = _cooperative_record(dict(updated or {}), historical_slate_date)
    if proven.get("current_slate_success_proof") != proof:
        raise RuntimeError("MLB_COOPERATIVE_CURRENT_SLATE_PROOF_WRITE_INVALID")
    return {
        **_cooperative_public_state(proven),
        "state": "DEFERRED_INSUFFICIENT_REMAINING_TIME",
        "currentSlateRanFirst": True,
        "remainingSeconds": remaining_seconds,
        "minimumRemainingSeconds": COOPERATIVE_REPLAY_MIN_REMAINING_SECONDS,
        "queueReadAttempted": True,
        "currentSlateSuccessProofPersisted": True,
        "activeLeaseMutationAllowed": False,
    }


def _claim_cooperative_replay(
    *,
    owner: str,
    context: Any,
    current_slate_response: Any,
    expected_slate_date: str,
    allow_fresh_prior_current_slate_proof: bool = False,
) -> tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    remaining = _remaining_seconds(context)
    if remaining < COOPERATIVE_REPLAY_MIN_REMAINING_SECONDS:
        if current_slate_response is not None:
            return None, _persist_current_slate_success_proof(
                current_slate_response=current_slate_response,
                expected_current_slate_date=expected_slate_date,
                remaining_seconds=remaining,
            )
        return None, {
            "version": COOPERATIVE_TERMINAL_REPLAY_VERSION,
            "state": "DEFERRED_INSUFFICIENT_REMAINING_TIME",
            "currentSlateRanFirst": False,
            "remainingSeconds": remaining,
            "minimumRemainingSeconds": COOPERATIVE_REPLAY_MIN_REMAINING_SECONDS,
            "queueReadAttempted": False,
            "activeLeaseMutationAllowed": False,
        }

    item = _read_cooperative_replay()
    if not item:
        return None, {
            "version": COOPERATIVE_TERMINAL_REPLAY_VERSION,
            "state": "NO_PENDING_REQUEST",
            "currentSlateRanFirst": True,
            "remainingSeconds": remaining,
            "queueReadAttempted": True,
            "activeLeaseMutationAllowed": False,
        }
    slate_date = str(item.get("slate_date_et") or "")
    item = _cooperative_record(item, slate_date)
    _strict_historical_slate_date(
        {
            "sport": "mlb",
            "run": COOPERATIVE_TERMINAL_REPLAY_RUN,
            "slateDateEt": slate_date,
            "force": True,
        }
    )
    state = str(item.get("state") or "")
    if state in {
        COOPERATIVE_REPLAY_COMPLETED,
        COOPERATIVE_REPLAY_ACKNOWLEDGED,
    }:
        return None, {
            **_cooperative_public_state(item),
            "currentSlateRanFirst": True,
            "queueReadAttempted": True,
            "remainingSeconds": remaining,
        }
    if state == COOPERATIVE_REPLAY_REVIEW_REQUIRED:
        return None, {
            **_cooperative_public_state(item),
            "reviewRequired": True,
            "reason": _cooperative_review_reason(item),
            "currentSlateRanFirst": True,
            "queueReadAttempted": True,
            "remainingSeconds": remaining,
            "staleClaimReclaimable": False,
            "activeLeaseMutationAllowed": False,
        }

    now = _utc_now()
    now_epoch = int(now.timestamp())
    request_epoch = int(item["requested_at_epoch"])
    request_id = str(item["request_id"])
    try:
        claim_expiry = int(item.get("claim_expires_at_epoch") or 0)
    except (TypeError, ValueError):
        claim_expiry = 0
    if state == COOPERATIVE_REPLAY_CLAIMED and claim_expiry > now_epoch:
        return None, {
            **_cooperative_public_state(item),
            "currentSlateRanFirst": True,
            "queueReadAttempted": True,
            "remainingSeconds": remaining,
            "staleClaimReclaimable": False,
        }

    current_slate_proof_carried = False
    if allow_fresh_prior_current_slate_proof:
        current_slate_proof_carried = _current_slate_success_proof_is_fresh(
            item,
            expected_current_slate_date=expected_slate_date,
        )
        if not current_slate_proof_carried:
            return None, {
                **_cooperative_public_state(item),
                "state": "CURRENT_SLATE_SUCCESS_PROOF_REQUIRED",
                "currentSlateRanFirst": False,
                "queueReadAttempted": True,
                "remainingSeconds": remaining,
                "currentSlateSuccessProofFresh": False,
                "activeLeaseMutationAllowed": False,
            }
    else:
        _assert_current_slate_processed_before_handoff(
            current_slate_response,
            expected_slate_date=expected_slate_date,
        )
    claim_expires = now + timedelta(
        seconds=min(
            LOCK_EXECUTION_LEASE_SECONDS,
            remaining + LOCK_EXECUTION_TIMEOUT_SAFETY_MARGIN_SECONDS,
        )
    )
    table = _cooperative_replay_table()
    try:
        updated = table.update_item(
            Key=_cooperative_replay_key(),
            ConditionExpression=(
                "record_type = :record_type AND "
                "coordination_version = :version AND "
                "slate_date_et = :slate_date AND "
                "requested_at_epoch = :request_epoch AND "
                "(request_id = :request_id OR attribute_not_exists(request_id)) AND "
                "(#state = :queued OR "
                "(#state = :claimed AND claim_expires_at_epoch <= :now_epoch))"
            ),
            UpdateExpression=(
                "SET request_id = :request_id, #state = :claimed, "
                "claim_owner = :owner, "
                "claim_acquired_at_utc = :now_utc, "
                "claim_acquired_at_epoch = :now_epoch, "
                "claim_expires_at_utc = :expires_utc, "
                "claim_expires_at_epoch = :expires_epoch"
            ),
            ExpressionAttributeNames={"#state": "state"},
            ExpressionAttributeValues={
                ":record_type": COOPERATIVE_TERMINAL_REPLAY_RECORD_TYPE,
                ":version": COOPERATIVE_TERMINAL_REPLAY_VERSION,
                ":slate_date": slate_date,
                ":request_epoch": request_epoch,
                ":request_id": request_id,
                ":queued": COOPERATIVE_REPLAY_QUEUED,
                ":claimed": COOPERATIVE_REPLAY_CLAIMED,
                ":owner": owner,
                ":now_utc": now.isoformat(),
                ":now_epoch": now_epoch,
                ":expires_utc": claim_expires.isoformat(),
                ":expires_epoch": int(claim_expires.timestamp()),
            },
            ReturnValues="ALL_NEW",
        ).get("Attributes")
    except BaseException as exc:
        if _error_code(exc) == "ConditionalCheckFailedException":
            observed = _cooperative_record(
                _read_cooperative_replay(),
                slate_date,
            )
            if (
                observed.get("state")
                == COOPERATIVE_REPLAY_REVIEW_REQUIRED
                and str(observed.get("slate_date_et") or "")
                == slate_date
                and observed.get("requested_at_epoch") == request_epoch
                and str(observed.get("request_id") or "") == request_id
            ):
                return None, {
                    **_cooperative_public_state(observed),
                    "reviewRequired": True,
                    "reason": _cooperative_review_reason(observed),
                    "currentSlateRanFirst": True,
                    "queueReadAttempted": True,
                    "remainingSeconds": remaining,
                    "staleClaimReclaimable": False,
                    "activeLeaseMutationAllowed": False,
                }
            return None, {
                "version": COOPERATIVE_TERMINAL_REPLAY_VERSION,
                "state": "CLAIM_LOST_TO_ANOTHER_OWNER",
                "currentSlateRanFirst": True,
                "queueReadAttempted": True,
                "remainingSeconds": remaining,
                "activeLeaseMutationAllowed": False,
            }
        raise
    claimed = _cooperative_record(dict(updated or {}), slate_date)
    return claimed, {
        **_cooperative_public_state(claimed),
        "currentSlateRanFirst": True,
        "queueReadAttempted": True,
        "remainingSeconds": remaining,
        "claimOwnerIsCurrentLeaseOwner": True,
        "currentSlateSuccessProofCarriedAcrossInvocation": (
            current_slate_proof_carried
        ),
    }


def _application_payload(response: Any) -> Dict[str, Any]:
    if not isinstance(response, dict):
        raise RuntimeError("MLB_COOPERATIVE_REPLAY_RESPONSE_INVALID")
    try:
        status_code = int(response.get("statusCode") or 200)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("MLB_COOPERATIVE_REPLAY_STATUS_INVALID") from exc
    body = response.get("body")
    try:
        payload = json.loads(body) if isinstance(body, str) else dict(body or {})
    except Exception as exc:
        raise RuntimeError("MLB_COOPERATIVE_REPLAY_BODY_INVALID") from exc
    if status_code < 200 or status_code >= 300 or not isinstance(payload, dict):
        raise RuntimeError("MLB_COOPERATIVE_REPLAY_APPLICATION_FAILED")
    return payload


def _assert_current_slate_processed_before_handoff(
    response: Any,
    *,
    expected_slate_date: str,
) -> None:
    payload = _application_payload(response)
    concurrency = payload.get("lockExecutionConcurrency") or {}
    inner_overlap = bool(
        str(payload.get("reason") or payload.get("status") or "")
        == "SKIPPED_OVERLAPPING_LOCK_EXECUTION"
        or str(payload.get("error") or "")
        == "MLB_LOCK_EXECUTION_ALREADY_RUNNING"
        or (
            isinstance(concurrency, dict)
            and concurrency.get("overlapSkipped") is True
        )
    )
    exact_current_slate_proven = bool(
        payload.get("ok") is True
        and str(payload.get("sport") or "") == "mlb"
        and str(payload.get("slateDateEt") or "") == expected_slate_date
    )
    if inner_overlap or not exact_current_slate_proven:
        raise RuntimeError(
            "MLB_CURRENT_SLATE_LOCK_NOT_PROCESSED_BEFORE_COOPERATIVE_REPLAY"
        )


def _validated_terminal_game_receipt(
    progress: Dict[str, Any],
    *,
    manifest_count: int,
    canonical_count: int,
    no_prediction_count: int,
    quarantine_count: int,
) -> tuple[list[Dict[str, Any]], str]:
    games = progress.get("terminalGames")
    if not isinstance(games, list) or len(games) != manifest_count:
        raise RuntimeError(
            "MLB_COOPERATIVE_REPLAY_TERMINAL_GAME_SET_INVALID"
        )
    normalized: list[Dict[str, Any]] = []
    official_seen: set[str] = set()
    states = {
        "LOCKED_CANONICAL": 0,
        "LOCKED_NO_PREDICTION_DATA": 0,
        "MISSED_LOCK_VALID_PRELOCK_CANDIDATE_NOT_PROMOTED": 0,
    }
    allowed_keys = {
        "index",
        "officialGamePk",
        "gameIdentity",
        "durableIdentity",
        "terminalState",
        "evidenceFingerprint",
    }
    for index, entry in enumerate(games):
        if not isinstance(entry, dict) or set(entry) != allowed_keys:
            raise RuntimeError(
                "MLB_COOPERATIVE_REPLAY_TERMINAL_GAME_SET_INVALID"
            )
        official_pk = str(entry.get("officialGamePk") or "")
        official_pk_number = _nonnegative_receipt_integer(
            entry.get("officialGamePk"),
            "terminal_game_official_game_pk",
        )
        entry_index = _nonnegative_receipt_integer(
            entry.get("index"),
            "terminal_game_index",
        )
        game_identity = str(entry.get("gameIdentity") or "")
        durable_identity = str(entry.get("durableIdentity") or "")
        state = str(entry.get("terminalState") or "")
        evidence_fingerprint = str(
            entry.get("evidenceFingerprint") or ""
        )
        if (
            entry_index != index
            or official_pk_number <= 0
            or not official_pk
            or official_pk in official_seen
            or not game_identity
            or not durable_identity
            or state not in states
            or re.fullmatch(
                r"[0-9a-f]{64}",
                evidence_fingerprint,
            )
            is None
        ):
            raise RuntimeError(
                "MLB_COOPERATIVE_REPLAY_TERMINAL_GAME_SET_INVALID"
            )
        official_seen.add(official_pk)
        states[state] += 1
        normalized.append(
            {
                "index": index,
                "officialGamePk": official_pk,
                "gameIdentity": game_identity,
                "durableIdentity": durable_identity,
                "terminalState": state,
                "evidenceFingerprint": evidence_fingerprint,
            }
        )
    if (
        states["LOCKED_CANONICAL"] != canonical_count
        or states["LOCKED_NO_PREDICTION_DATA"]
        != no_prediction_count
        or states[
            "MISSED_LOCK_VALID_PRELOCK_CANDIDATE_NOT_PROMOTED"
        ]
        != quarantine_count
    ):
        raise RuntimeError(
            "MLB_COOPERATIVE_REPLAY_TERMINAL_GAME_SET_INVALID"
        )
    fingerprint = hashlib.sha256(
        json.dumps(
            normalized,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if (
        str(progress.get("terminalGameSetFingerprint") or "")
        != fingerprint
    ):
        raise RuntimeError(
            "MLB_COOPERATIVE_REPLAY_TERMINAL_GAME_SET_INVALID"
        )
    return normalized, fingerprint


def _nonnegative_receipt_integer(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise RuntimeError(f"MLB_COOPERATIVE_REPLAY_{field.upper()}_INVALID")
    try:
        numeric = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise RuntimeError(
            f"MLB_COOPERATIVE_REPLAY_{field.upper()}_INVALID"
        ) from exc
    if (
        not numeric.is_finite()
        or numeric < 0
        or numeric != numeric.to_integral_value()
    ):
        raise RuntimeError(
            f"MLB_COOPERATIVE_REPLAY_{field.upper()}_INVALID"
        )
    return int(numeric)


def _terminal_replay_receipt(
    response: Any,
    slate_date: str,
) -> Dict[str, Any]:
    payload = _application_payload(response)
    if (
        payload.get("ok") is not True
        or str(payload.get("sport") or "") != "mlb"
        or str(payload.get("slateDateEt") or "") != slate_date
        or payload.get("terminalChunkVersion")
        != COOPERATIVE_TERMINAL_CHUNK_VERSION
        or payload.get("verificationPhase") != "VERIFY"
        or payload.get("durableTerminalVerificationComplete") is not True
        or payload.get("atomicDurableProofRequired") is not True
        or payload.get("completionMutationLeaseRequired") is not True
        or payload.get("postStartPredictionCreationAllowed") is not False
        or payload.get("immutablePredictionRewriteAllowed") is not False
        or payload.get("directWorkflowTableWrite") is not False
        or payload.get("productionAuthorityChanged") is not False
        or payload.get("lockStatusComplete") is not True
        or payload.get("dailyCardComplete") is not True
    ):
        raise RuntimeError("MLB_COOPERATIVE_REPLAY_RESULT_UNHEALTHY")

    checkpoint_fingerprint = str(
        payload.get("checkpointFingerprint") or ""
    )
    manifest_fingerprint = str(
        payload.get("manifestFingerprint") or ""
    )
    if (
        re.fullmatch(r"[0-9a-f]{64}", checkpoint_fingerprint) is None
        or re.fullmatch(r"[0-9a-f]{64}", manifest_fingerprint) is None
    ):
        raise RuntimeError(
            "MLB_COOPERATIVE_REPLAY_COMPLETION_FINGERPRINT_MISSING"
        )

    repair = payload.get("missedLockTerminalReconciliation")
    if not isinstance(repair, dict):
        raise RuntimeError("MLB_COOPERATIVE_REPLAY_REPAIR_PROOF_MISSING")
    progress = repair.get("progressAfter")
    payload_progress = payload.get("perGameLockProgress")
    if (
        not isinstance(progress, dict)
        or not isinstance(payload_progress, dict)
        or payload_progress != progress
    ):
        raise RuntimeError("MLB_COOPERATIVE_REPLAY_PROGRESS_PROOF_MISSING")
    unresolved = repair.get("unresolved")
    if not isinstance(unresolved, list) or unresolved:
        raise RuntimeError("MLB_COOPERATIVE_REPLAY_UNRESOLVED")

    manifest_count = _nonnegative_receipt_integer(
        progress.get("manifestGameCount"), "manifest_game_count"
    )
    processed_count = _nonnegative_receipt_integer(
        progress.get("processedGameCount"), "processed_game_count"
    )
    verified_count = _nonnegative_receipt_integer(
        progress.get("verifiedGameCount"), "verified_game_count"
    )
    verification_index = _nonnegative_receipt_integer(
        progress.get("verificationIndex"), "verification_index"
    )
    atomic_item_count = _nonnegative_receipt_integer(
        progress.get("atomicDurableItemCount"),
        "atomic_durable_item_count",
    )
    atomic_read_set_fingerprint = str(
        progress.get("atomicDurableReadSetFingerprint") or ""
    )
    canonical_count = _nonnegative_receipt_integer(
        progress.get("canonicalCount"), "canonical_count"
    )
    no_prediction_count = _nonnegative_receipt_integer(
        progress.get("noPredictionDataCount"),
        "no_prediction_data_count",
    )
    quarantine_count = _nonnegative_receipt_integer(
        progress.get("missedLockValidPrelockQuarantineCount"),
        "missed_lock_valid_prelock_quarantine_count",
    )
    lock_outcome_count = _nonnegative_receipt_integer(
        progress.get("lockOutcomeCount"), "lock_outcome_count"
    )
    missed = _nonnegative_receipt_integer(
        progress.get("missedCount"), "missed_count"
    )
    due = _nonnegative_receipt_integer(
        progress.get("dueMissingCount"), "due_missing_count"
    )
    reconciled = _nonnegative_receipt_integer(
        repair.get("reconciledCount"), "reconciled_count"
    )
    terminal_games, terminal_game_set_fingerprint = (
        _validated_terminal_game_receipt(
            progress,
            manifest_count=manifest_count,
            canonical_count=canonical_count,
            no_prediction_count=no_prediction_count,
            quarantine_count=quarantine_count,
        )
    )
    remaining = _nonnegative_receipt_integer(
        repair.get("remainingMissedCount", missed),
        "remaining_missed_count",
    )
    cached_idempotent = bool(
        str(payload.get("reason") or "")
        == "POST_WINDOW_TERMINAL_STATUS_ALREADY_RECONCILED"
        and reconciled == 0
    )
    if (
        manifest_count <= 0
        or processed_count != manifest_count
        or verified_count != manifest_count
        or verification_index != manifest_count
        or (
            canonical_count + no_prediction_count + quarantine_count
            != manifest_count
        )
        or lock_outcome_count != manifest_count
        or atomic_item_count < manifest_count
        or atomic_item_count > COOPERATIVE_TERMINAL_ATOMIC_MAX_ITEMS
        or re.fullmatch(
            r"[0-9a-f]{64}",
            atomic_read_set_fingerprint,
        )
        is None
        or progress.get("verificationComplete") is not True
        or progress.get("atomicDurableProofRequired") is not True
        or str(progress.get("manifestFingerprint") or "")
        != manifest_fingerprint
        or str(progress.get("checkpointFingerprint") or "")
        != checkpoint_fingerprint
        or re.fullmatch(
            r"[0-9a-f]{64}",
            str(
                progress.get(
                    "manifestAuthorityEvidenceFingerprint"
                )
                or ""
            ),
        )
        is None
        or re.fullmatch(
            r"[0-9a-f]{64}",
            str(progress.get("providerManifestFingerprint") or ""),
        )
        is None
        or _nonnegative_receipt_integer(
            payload.get("manifestGameCount"), "top_manifest_game_count"
        )
        != manifest_count
        or _nonnegative_receipt_integer(
            payload.get("processedGameCount"), "top_processed_game_count"
        )
        != processed_count
        or _nonnegative_receipt_integer(
            payload.get("verifiedGameCount"), "top_verified_game_count"
        )
        != verified_count
        or _nonnegative_receipt_integer(
            payload.get("verificationIndex"), "top_verification_index"
        )
        != verification_index
        or _nonnegative_receipt_integer(
            payload.get("lockOutcomeCount"), "top_lock_outcome_count"
        )
        != lock_outcome_count
        or _nonnegative_receipt_integer(
            payload.get("missedGameCount"), "top_missed_game_count"
        )
        != 0
        or _nonnegative_receipt_integer(
            payload.get("atomicDurableItemCount"),
            "top_atomic_durable_item_count",
        )
        != atomic_item_count
        or str(
            payload.get("atomicDurableReadSetFingerprint") or ""
        )
        != atomic_read_set_fingerprint
        or repair.get("ok") is not True
        or repair.get("version") != COOPERATIVE_TERMINAL_CHUNK_VERSION
        or str(repair.get("slateDateEt") or "") != slate_date
        or _nonnegative_receipt_integer(
            repair.get("manifestGameCount"), "repair_manifest_game_count"
        )
        != manifest_count
        or _nonnegative_receipt_integer(
            repair.get("processedGameCount"), "repair_processed_game_count"
        )
        != processed_count
        or _nonnegative_receipt_integer(
            repair.get("verifiedGameCount"), "repair_verified_game_count"
        )
        != verified_count
        or _nonnegative_receipt_integer(
            repair.get("verificationIndex"), "repair_verification_index"
        )
        != verification_index
        or repair.get("durableTerminalVerificationComplete") is not True
        or repair.get("atomicDurableProofRequired") is not True
        or repair.get("completionMutationLeaseRequired") is not True
        or repair.get("postStartPredictionCreationAllowed") is not False
        or repair.get("candidateIntegrityFailuresRelabeled") is not False
        or _nonnegative_receipt_integer(
            repair.get("atomicDurableItemCount"),
            "repair_atomic_durable_item_count",
        )
        != atomic_item_count
        or str(
            repair.get("atomicDurableReadSetFingerprint") or ""
        )
        != atomic_read_set_fingerprint
        or _nonnegative_receipt_integer(
            repair.get("missedLockValidPrelockQuarantineCount"),
            "repair_quarantine_count",
        )
        != quarantine_count
        or remaining
        or missed
        or due
        or (reconciled <= 0 and not cached_idempotent)
    ):
        raise RuntimeError(
            "MLB_COOPERATIVE_REPLAY_REPAIR_PROOF_UNHEALTHY"
        )

    safe_progress = {
        "manifestGameCount": manifest_count,
        "processedGameCount": processed_count,
        "verifiedGameCount": verified_count,
        "verificationIndex": verification_index,
        "verificationComplete": True,
        "atomicDurableItemCount": atomic_item_count,
        "atomicDurableReadSetFingerprint": (
            atomic_read_set_fingerprint
        ),
        "atomicDurableProofRequired": True,
        "canonicalCount": canonical_count,
        "noPredictionDataCount": no_prediction_count,
        "missedLockValidPrelockQuarantineCount": quarantine_count,
        "lockOutcomeCount": lock_outcome_count,
        "missedCount": 0,
        "dueMissingCount": 0,
        "manifestFingerprint": manifest_fingerprint,
        "checkpointFingerprint": checkpoint_fingerprint,
        "manifestAuthorityEvidenceFingerprint": str(
            progress.get("manifestAuthorityEvidenceFingerprint") or ""
        ),
        "providerManifestFingerprint": str(
            progress.get("providerManifestFingerprint") or ""
        ),
        "terminalGames": terminal_games,
        "terminalGameSetFingerprint": terminal_game_set_fingerprint,
    }
    safe_repair = {
        "ok": True,
        "version": COOPERATIVE_TERMINAL_CHUNK_VERSION,
        "slateDateEt": slate_date,
        "manifestGameCount": manifest_count,
        "processedGameCount": processed_count,
        "verifiedGameCount": verified_count,
        "verificationIndex": verification_index,
        "durableTerminalVerificationComplete": True,
        "atomicDurableProofRequired": True,
        "atomicDurableItemCount": atomic_item_count,
        "atomicDurableReadSetFingerprint": (
            atomic_read_set_fingerprint
        ),
        "completionMutationLeaseRequired": True,
        "reconciledCount": reconciled,
        "missedLockValidPrelockQuarantineCount": quarantine_count,
        "remainingMissedCount": 0,
        "unresolved": [],
        "progressAfter": safe_progress,
        "postStartPredictionCreationAllowed": False,
        "candidateIntegrityFailuresRelabeled": False,
    }
    return {
        "ok": True,
        "sport": "mlb",
        "slateDateEt": slate_date,
        "reason": str(payload.get("reason") or "")[:160],
        "terminalChunkVersion": COOPERATIVE_TERMINAL_CHUNK_VERSION,
        "checkpointFingerprint": checkpoint_fingerprint,
        "manifestFingerprint": manifest_fingerprint,
        "providerManifestFingerprint": safe_progress[
            "providerManifestFingerprint"
        ],
        "manifestGameCount": manifest_count,
        "processedGameCount": processed_count,
        "verifiedGameCount": verified_count,
        "verificationIndex": verification_index,
        "lockOutcomeCount": lock_outcome_count,
        "missedGameCount": 0,
        "lockStatusComplete": True,
        "dailyCardComplete": True,
        "verificationPhase": "VERIFY",
        "durableTerminalVerificationComplete": True,
        "atomicDurableProofRequired": True,
        "atomicDurableItemCount": atomic_item_count,
        "atomicDurableReadSetFingerprint": (
            atomic_read_set_fingerprint
        ),
        "completionMutationLeaseRequired": True,
        "perGameLockProgress": safe_progress,
        "missedLockTerminalReconciliation": safe_repair,
        "postStartPredictionCreationAllowed": False,
        "immutablePredictionRewriteAllowed": False,
        "directWorkflowTableWrite": False,
        "productionAuthorityChanged": False,
        "cooperativeReceiptRedacted": True,
    }


def _checkpoint_terminal_game_receipt(
    progress: Mapping[str, Any],
) -> tuple[list[Dict[str, Any]], str]:
    manifest_count = _nonnegative_receipt_integer(
        progress.get("manifestGameCount"),
        "checkpoint_manifest_game_count",
    )
    processed = progress.get("processedGames")
    if not isinstance(processed, list) or len(processed) != manifest_count:
        raise RuntimeError(
            "MLB_COOPERATIVE_REPLAY_COMPLETE_PROGRESS_INVALID"
        )
    normalized: list[Dict[str, Any]] = []
    official_seen: set[str] = set()
    for index, entry in enumerate(processed):
        if not isinstance(entry, Mapping):
            raise RuntimeError(
                "MLB_COOPERATIVE_REPLAY_COMPLETE_PROGRESS_INVALID"
            )
        official_pk = str(entry.get("officialGamePk") or "")
        official_pk_number = _nonnegative_receipt_integer(
            entry.get("officialGamePk"),
            "checkpoint_terminal_game_official_game_pk",
        )
        game_identity = str(entry.get("gameIdentity") or "")
        durable_identity = str(entry.get("durableIdentity") or "")
        terminal_state = str(entry.get("terminalState") or "")
        evidence = entry.get("durableEvidence")
        evidence_fingerprint = (
            str(evidence.get("evidenceFingerprint") or "")
            if isinstance(evidence, Mapping)
            else ""
        )
        if (
            official_pk_number <= 0
            or not official_pk
            or official_pk in official_seen
            or not game_identity
            or not durable_identity
            or terminal_state
            not in {
                "LOCKED_CANONICAL",
                "LOCKED_NO_PREDICTION_DATA",
                "MISSED_LOCK_VALID_PRELOCK_CANDIDATE_NOT_PROMOTED",
            }
            or re.fullmatch(r"[0-9a-f]{64}", evidence_fingerprint)
            is None
        ):
            raise RuntimeError(
                "MLB_COOPERATIVE_REPLAY_COMPLETE_PROGRESS_INVALID"
            )
        official_seen.add(official_pk)
        normalized.append(
            {
                "index": index,
                "officialGamePk": official_pk,
                "gameIdentity": game_identity,
                "durableIdentity": durable_identity,
                "terminalState": terminal_state,
                "evidenceFingerprint": evidence_fingerprint,
            }
        )
    fingerprint = hashlib.sha256(
        json.dumps(
            normalized,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return normalized, fingerprint


def _complete_cooperative_replay(
    *, item: Dict[str, Any], owner: str, response: Any
) -> Dict[str, Any]:
    slate_date = str(item.get("slate_date_et") or "")
    if str(item.get("claim_owner") or "") != owner:
        raise RuntimeError("MLB_COOPERATIVE_REPLAY_COMPLETE_FAILED")
    request_id = str(item.get("request_id") or "")
    request_epoch = _nonnegative_receipt_integer(
        item.get("requested_at_epoch"), "request_epoch"
    )
    expected_progress = item.get("terminal_replay_progress")
    if not isinstance(expected_progress, dict):
        observed = _cooperative_record(
            _read_cooperative_replay(),
            slate_date,
        )
        if (
            observed.get("state") == COOPERATIVE_REPLAY_CLAIMED
            and observed.get("claim_owner") == owner
            and observed.get("requested_at_epoch") == request_epoch
            and observed.get("request_id") == request_id
            and isinstance(
                observed.get("terminal_replay_progress"), dict
            )
        ):
            item = observed
            expected_progress = observed["terminal_replay_progress"]
    if (
        not request_id
        or request_epoch <= 0
        or not isinstance(expected_progress, dict)
        or expected_progress.get("version")
        != COOPERATIVE_TERMINAL_CHUNK_VERSION
        or expected_progress.get("verificationComplete") is not True
        or _nonnegative_receipt_integer(
            expected_progress.get("verificationIndex"),
            "verification_index",
        )
        != _nonnegative_receipt_integer(
            expected_progress.get("manifestGameCount"),
            "manifest_game_count",
        )
        or _nonnegative_receipt_integer(
            expected_progress.get("requestEpoch"),
            "progress_request_epoch",
        )
        != request_epoch
        or str(expected_progress.get("requestId") or "") != request_id
    ):
        raise RuntimeError(
            "MLB_COOPERATIVE_REPLAY_COMPLETE_PROGRESS_INVALID"
        )
    checkpoint_validator = getattr(
        mlb_daily_pick_lock,
        "validate_cooperative_terminal_completion_checkpoint",
        None,
    )
    if (
        not callable(checkpoint_validator)
        or getattr(
            mlb_daily_pick_lock,
            "MLB_COOPERATIVE_TERMINAL_COMPLETION_HANDOFF_VERSION",
            None,
        )
        != COOPERATIVE_TERMINAL_COMPLETION_HANDOFF_VERSION
    ):
        raise RuntimeError(
            "MLB_COOPERATIVE_REPLAY_COMPLETE_VALIDATOR_NOT_READY"
        )
    try:
        validated_progress = checkpoint_validator(expected_progress)
    except BaseException as exc:
        raise RuntimeError(
            "MLB_COOPERATIVE_REPLAY_COMPLETE_PROGRESS_INVALID"
        ) from exc
    if (
        not isinstance(validated_progress, tuple)
        or len(validated_progress) != 3
        or validated_progress[0] != expected_progress
        or not isinstance(validated_progress[1], list)
        or re.fullmatch(
            r"[0-9a-f]{64}",
            str(validated_progress[2] or ""),
        )
        is None
    ):
        raise RuntimeError(
            "MLB_COOPERATIVE_REPLAY_COMPLETE_PROGRESS_INVALID"
        )

    receipt = _terminal_replay_receipt(response, slate_date)
    receipt_progress = receipt["perGameLockProgress"]
    expected_terminal_games, expected_terminal_game_fingerprint = (
        _checkpoint_terminal_game_receipt(expected_progress)
    )
    expected_manifest_authority = expected_progress.get(
        "manifestAuthority"
    )
    if not isinstance(expected_manifest_authority, Mapping):
        raise RuntimeError(
            "MLB_COOPERATIVE_REPLAY_COMPLETE_PROGRESS_INVALID"
        )
    expected_authority_fingerprint = str(
        expected_manifest_authority.get(
            "authorityEvidenceFingerprint"
        )
        or ""
    )
    expected_provider_manifest_fingerprint = str(
        expected_manifest_authority.get("fingerprint") or ""
    )
    expected_fields = {
        "manifestGameCount": "manifestGameCount",
        "processedGameCount": "processedGameCount",
        "verifiedGameCount": "verifiedGameCount",
        "verificationIndex": "verificationIndex",
        "canonicalCount": "canonicalCount",
        "noPredictionDataCount": "noPredictionDataCount",
        "missedLockValidPrelockQuarantineCount": (
            "missedLockValidPrelockQuarantineCount"
        ),
        "lockOutcomeCount": "terminalCount",
    }
    if (
        receipt.get("checkpointFingerprint")
        != expected_progress.get("checkpointFingerprint")
        or receipt.get("manifestFingerprint")
        != expected_progress.get("manifestFingerprint")
        or receipt_progress.get("terminalGames")
        != expected_terminal_games
        or receipt_progress.get("terminalGameSetFingerprint")
        != expected_terminal_game_fingerprint
        or receipt_progress.get("manifestAuthorityEvidenceFingerprint")
        != expected_authority_fingerprint
        or receipt_progress.get("providerManifestFingerprint")
        != expected_provider_manifest_fingerprint
        or receipt.get("providerManifestFingerprint")
        != expected_provider_manifest_fingerprint
        or _nonnegative_receipt_integer(
            receipt_progress.get("atomicDurableItemCount"),
            "receipt_atomic_durable_item_count",
        )
        != len(validated_progress[1])
        or receipt_progress.get(
            "atomicDurableReadSetFingerprint"
        )
        != validated_progress[2]
        or any(
        _nonnegative_receipt_integer(
            receipt_progress.get(receipt_field),
            f"receipt_{receipt_field}",
        )
        != _nonnegative_receipt_integer(
            expected_progress.get(progress_field),
            f"progress_{progress_field}",
        )
        for receipt_field, progress_field in expected_fields.items()
        )
    ):
        raise RuntimeError(
            "MLB_COOPERATIVE_REPLAY_RECEIPT_CHECKPOINT_MISMATCH"
        )
    now = _utc_now()
    table = _cooperative_replay_table()
    try:
        updated = table.update_item(
            Key=_cooperative_replay_key(),
            ConditionExpression=(
                "record_type = :record_type AND "
                "coordination_version = :version AND "
                "slate_date_et = :slate_date AND "
                "requested_at_epoch = :request_epoch AND "
                "request_id = :request_id AND "
                "terminal_replay_progress = :expected_progress AND "
                "#state = :claimed AND claim_owner = :owner"
            ),
            UpdateExpression=(
                "SET #state = :completed, completed_at_utc = :now_utc, "
                "completed_at_epoch = :now_epoch, replay_receipt = :receipt "
                "REMOVE current_slate_success_proof, claim_owner, "
                "claim_acquired_at_utc, "
                "claim_acquired_at_epoch, claim_expires_at_utc, "
                "claim_expires_at_epoch"
            ),
            ExpressionAttributeNames={"#state": "state"},
            ExpressionAttributeValues={
                ":record_type": COOPERATIVE_TERMINAL_REPLAY_RECORD_TYPE,
                ":version": COOPERATIVE_TERMINAL_REPLAY_VERSION,
                ":slate_date": slate_date,
                ":request_epoch": request_epoch,
                ":request_id": request_id,
                ":expected_progress": expected_progress,
                ":claimed": COOPERATIVE_REPLAY_CLAIMED,
                ":completed": COOPERATIVE_REPLAY_COMPLETED,
                ":owner": owner,
                ":now_utc": now.isoformat(),
                ":now_epoch": int(now.timestamp()),
                ":receipt": receipt,
            },
            ReturnValues="ALL_NEW",
        ).get("Attributes")
    except BaseException as exc:
        # An ambiguous completion is never overwritten or requeued.  A strong
        # read accepts only the exact completed receipt; otherwise the stale
        # claim remains safely reclaimable after its deadline.
        observed = _cooperative_record(_read_cooperative_replay(), slate_date)
        if (
            observed.get("record_type")
            == COOPERATIVE_TERMINAL_REPLAY_RECORD_TYPE
            and observed.get("coordination_version")
            == COOPERATIVE_TERMINAL_REPLAY_VERSION
            and observed.get("state") == COOPERATIVE_REPLAY_COMPLETED
            and str(observed.get("slate_date_et") or "") == slate_date
            and observed.get("requested_at_epoch") == request_epoch
            and str(observed.get("request_id") or "") == request_id
            and observed.get("terminal_replay_progress")
            == expected_progress
            and observed.get("replay_receipt") == receipt
        ):
            updated = observed
        else:
            raise RuntimeError(
                "MLB_COOPERATIVE_REPLAY_COMPLETE_FAILED"
            ) from exc
    completed = _cooperative_record(dict(updated or {}), slate_date)
    if (
        completed.get("record_type")
        != COOPERATIVE_TERMINAL_REPLAY_RECORD_TYPE
        or completed.get("coordination_version")
        != COOPERATIVE_TERMINAL_REPLAY_VERSION
        or completed.get("state") != COOPERATIVE_REPLAY_COMPLETED
        or str(completed.get("slate_date_et") or "") != slate_date
        or completed.get("requested_at_epoch") != request_epoch
        or str(completed.get("request_id") or "") != request_id
        or completed.get("terminal_replay_progress")
        != expected_progress
        or completed.get("replay_receipt") != receipt
    ):
        raise RuntimeError("MLB_COOPERATIVE_REPLAY_COMPLETE_STATE_INVALID")
    return _cooperative_public_state(completed)



def _checkpoint_cooperative_replay(
    *,
    item: Dict[str, Any],
    owner: str,
    progress: Dict[str, Any],
    failed: bool,
    review_required: bool = False,
) -> Dict[str, Any]:
    slate_date = str(item.get("slate_date_et") or "")
    request_id = str(item.get("request_id") or "")
    request_epoch = _nonnegative_receipt_integer(
        item.get("requested_at_epoch"), "request_epoch"
    )
    prior_progress = item.get("terminal_replay_progress")
    review_progress = bool(
        review_required
        and isinstance(progress, dict)
        and progress.get("version")
        in {
            COOPERATIVE_TERMINAL_CHUNK_V3_VERSION,
            COOPERATIVE_TERMINAL_CHUNK_VERSION,
        }
        and str(progress.get("slateDateEt") or "") == slate_date
        and _nonnegative_receipt_integer(
            progress.get("requestEpoch"),
            "review_progress_request_epoch",
        )
        == request_epoch
        and str(progress.get("requestId") or "") == request_id
        and progress.get("postStartPredictionCreationAllowed") is False
        and progress.get("immutablePredictionRewriteAllowed") is False
        and progress.get("productionAuthorityChanged") is False
    )
    normal_progress = bool(
        not review_required
        and isinstance(progress, dict)
        and progress.get("version") == COOPERATIVE_TERMINAL_CHUNK_VERSION
        and str(progress.get("slateDateEt") or "") == slate_date
        and _nonnegative_receipt_integer(
            progress.get("requestEpoch"), "progress_request_epoch"
        )
        == request_epoch
        and str(progress.get("requestId") or "") == request_id
        and progress.get("postStartPredictionCreationAllowed") is False
        and progress.get("immutablePredictionRewriteAllowed") is False
        and progress.get("productionAuthorityChanged") is False
    )
    if (
        not request_id
        or request_epoch <= 0
        or not (review_progress or normal_progress)
    ):
        raise RuntimeError("MLB_COOPERATIVE_TERMINAL_CHECKPOINT_INVALID")
    now = _utc_now()
    target_state = (
        COOPERATIVE_REPLAY_REVIEW_REQUIRED
        if review_required
        else COOPERATIVE_REPLAY_QUEUED
    )
    last_attempt = progress.get("lastAttempt")
    stage = (
        str(last_attempt.get("stage") or "")[:160]
        if isinstance(last_attempt, dict)
        else ""
    )
    status = (
        str(last_attempt.get("status") or "")[:160]
        if isinstance(last_attempt, dict)
        else ""
    )
    set_parts = [
        "#state = :target_state",
        "terminal_replay_progress = :progress",
        "last_chunk_at_utc = :now_utc",
        "last_chunk_at_epoch = :now_epoch",
        "last_chunk_stage = :chunk_stage",
        "last_chunk_status = :chunk_status",
    ]
    if failed:
        set_parts.extend(
            [
                "last_failure_at_utc = :now_utc",
                "last_failure_at_epoch = :now_epoch",
            ]
        )
    expression_values = {
        ":record_type": COOPERATIVE_TERMINAL_REPLAY_RECORD_TYPE,
        ":version": COOPERATIVE_TERMINAL_REPLAY_VERSION,
        ":slate_date": slate_date,
        ":request_epoch": request_epoch,
        ":request_id": request_id,
        ":claimed": COOPERATIVE_REPLAY_CLAIMED,
        ":target_state": target_state,
        ":owner": owner,
        ":progress": progress,
        ":now_utc": now.isoformat(),
        ":now_epoch": int(now.timestamp()),
        ":chunk_stage": stage,
        ":chunk_status": status,
    }
    if prior_progress is not None:
        expression_values[":prior_progress"] = prior_progress
    try:
        updated = _cooperative_replay_table().update_item(
            Key=_cooperative_replay_key(),
            ConditionExpression=(
                "record_type = :record_type AND "
                "coordination_version = :version AND "
                "slate_date_et = :slate_date AND "
                "requested_at_epoch = :request_epoch AND "
                "request_id = :request_id AND "
                + (
                    "terminal_replay_progress = :prior_progress AND "
                    if prior_progress is not None
                    else "attribute_not_exists(terminal_replay_progress) AND "
                )
                + "#state = :claimed AND claim_owner = :owner"
            ),
            UpdateExpression=(
                "SET "
                + ", ".join(set_parts)
                + " REMOVE current_slate_success_proof, claim_owner, "
                "claim_acquired_at_utc, claim_acquired_at_epoch, "
                "claim_expires_at_utc, claim_expires_at_epoch"
            ),
            ExpressionAttributeNames={"#state": "state"},
            ExpressionAttributeValues=expression_values,
            ReturnValues="ALL_NEW",
        ).get("Attributes")
    except BaseException as exc:
        observed = _cooperative_record(
            _read_cooperative_replay(),
            slate_date,
        )
        if (
            observed.get("record_type")
            == COOPERATIVE_TERMINAL_REPLAY_RECORD_TYPE
            and observed.get("coordination_version")
            == COOPERATIVE_TERMINAL_REPLAY_VERSION
            and observed.get("state") == target_state
            and str(observed.get("slate_date_et") or "") == slate_date
            and observed.get("requested_at_epoch") == request_epoch
            and str(observed.get("request_id") or "") == request_id
            and observed.get("terminal_replay_progress") == progress
        ):
            updated = observed
        else:
            raise RuntimeError(
                "MLB_COOPERATIVE_TERMINAL_CHECKPOINT_FAILED"
            ) from exc
    checkpointed = _cooperative_record(dict(updated or {}), slate_date)
    if (
        checkpointed.get("record_type")
        != COOPERATIVE_TERMINAL_REPLAY_RECORD_TYPE
        or checkpointed.get("coordination_version")
        != COOPERATIVE_TERMINAL_REPLAY_VERSION
        or checkpointed.get("state") != target_state
        or str(checkpointed.get("slate_date_et") or "") != slate_date
        or checkpointed.get("requested_at_epoch") != request_epoch
        or str(checkpointed.get("request_id") or "") != request_id
        or checkpointed.get("terminal_replay_progress") != progress
    ):
        raise RuntimeError(
            "MLB_COOPERATIVE_TERMINAL_CHECKPOINT_STATE_INVALID"
        )
    return _cooperative_public_state(checkpointed)

def _requeue_cooperative_replay(item: Dict[str, Any], owner: str) -> bool:
    slate_date = str(item.get("slate_date_et") or "")
    request_id = str(item.get("request_id") or "")
    try:
        request_epoch = _nonnegative_receipt_integer(
            item.get("requested_at_epoch"), "request_epoch"
        )
    except RuntimeError:
        return False
    if not slate_date or not request_id or request_epoch <= 0:
        return False
    now = _utc_now()
    try:
        _cooperative_replay_table().update_item(
            Key=_cooperative_replay_key(),
            ConditionExpression=(
                "record_type = :record_type AND "
                "coordination_version = :version AND "
                "slate_date_et = :slate_date AND "
                "requested_at_epoch = :request_epoch AND "
                "request_id = :request_id AND "
                "#state = :claimed AND claim_owner = :owner"
            ),
            UpdateExpression=(
                "SET #state = :queued, last_failure_at_utc = :now_utc, "
                "last_failure_at_epoch = :now_epoch "
                "REMOVE current_slate_success_proof, claim_owner, "
                "claim_acquired_at_utc, "
                "claim_acquired_at_epoch, claim_expires_at_utc, "
                "claim_expires_at_epoch"
            ),
            ExpressionAttributeNames={"#state": "state"},
            ExpressionAttributeValues={
                ":record_type": COOPERATIVE_TERMINAL_REPLAY_RECORD_TYPE,
                ":version": COOPERATIVE_TERMINAL_REPLAY_VERSION,
                ":slate_date": slate_date,
                ":request_epoch": request_epoch,
                ":request_id": request_id,
                ":claimed": COOPERATIVE_REPLAY_CLAIMED,
                ":queued": COOPERATIVE_REPLAY_QUEUED,
                ":owner": owner,
                ":now_utc": now.isoformat(),
                ":now_epoch": int(now.timestamp()),
            },
        )
    except BaseException:
        return False
    return True


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
    return lifecycle_counts.reconcile_http_response(
        out,
        row_field="perGameStatus",
    )


def _attach_cooperative_owner_status(
    response: Any, status: Optional[Dict[str, Any]]
) -> Any:
    if status is None or not isinstance(response, dict):
        return response
    out = dict(response)
    body = out.get("body")
    try:
        payload = json.loads(body) if isinstance(body, str) else dict(body or {})
    except Exception:
        payload = {"rawBodyRedacted": True}
    payload["cooperativeTerminalReplayOwnerExecution"] = dict(status)
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

    # A manually invoked exact historical repair never contests or mutates the
    # active execution lease.  It writes one bounded control-plane request and
    # polls that same record.  Only a normal EventBridge daily owner can claim
    # and execute it later under the existing lease.
    if _is_cooperative_replay_request(event, method):
        try:
            if event.get("acknowledgeCooperativeCompletion") is True:
                cooperative = _acknowledge_cooperative_replay(event)
            else:
                cooperative = _enqueue_or_read_cooperative_replay(event)
        except BaseException as exc:
            return _failure_response(
                event,
                500,
                {
                    "ok": False,
                    "sport": "mlb",
                    "error": "MLB_COOPERATIVE_TERMINAL_REPLAY_FAILED_CLOSED",
                    "errorCode": _error_code(exc),
                    "mutatingRunAttempted": False,
                    "activeLeaseMutationAllowed": False,
                    "postStartPredictionCreationAllowed": False,
                    "immutablePredictionRewriteAllowed": False,
                    "directWorkflowTableWrite": False,
                },
            )
        return _attach_preservation_status(_resp(200, cooperative))

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
    cooperative_owner_status: Optional[Dict[str, Any]] = None
    primary_error: Optional[BaseException] = None
    release_error: Optional[BaseException] = None
    retained_completion_lease: Optional[Dict[str, Any]] = None
    completion_lease_release_error: Optional[BaseException] = None
    try:
        # A fresh, request-bound proof may carry a successful current-slate run
        # across exactly one short EventBridge handoff.  This prevents a long
        # current run from starving the historical replay of its own bounded
        # execution budget.  Without that durable proof, current-slate work
        # still runs and succeeds before any historical claim.
        claimed: Optional[Dict[str, Any]] = None
        current_slate_proof_carried = False
        canonical_eventbridge_owner = _is_eventbridge_daily_lock_owner(event)
        if canonical_eventbridge_owner:
            claimed, cooperative_owner_status = _claim_cooperative_replay(
                owner=owner,
                context=context,
                current_slate_response=None,
                expected_slate_date=slate_date_et,
                allow_fresh_prior_current_slate_proof=True,
            )
            current_slate_proof_carried = claimed is not None

        if claimed is None:
            response = mlb_daily_pick_lock.lambda_handler(event, context)
            _raise_scheduled_delegate_failure(event, response)
            if canonical_eventbridge_owner:
                claimed, cooperative_owner_status = _claim_cooperative_replay(
                    owner=owner,
                    context=context,
                    current_slate_response=response,
                    expected_slate_date=slate_date_et,
                )
        else:
            response = _resp(
                200,
                {
                    "ok": True,
                    "sport": "mlb",
                    "slateDateEt": slate_date_et,
                    "status": (
                        "CURRENT_SLATE_PROVEN_BY_PRIOR_EVENTBRIDGE_OWNER"
                    ),
                    "scheduledInvocation": True,
                    "currentSlateProcessedByPriorEventBridgeOwner": True,
                    "postStartPredictionCreationAllowed": False,
                    "immutablePredictionRewriteAllowed": False,
                    "productionAuthorityChanged": False,
                },
            )

        if claimed is not None:
            replay_event = {
                "sport": "mlb",
                "run": COOPERATIVE_TERMINAL_REPLAY_RUN,
                "slateDateEt": str(claimed.get("slate_date_et") or ""),
                # External admission remains force=True, but the canonical
                # EventBridge owner never executes force generation.  The
                # installed chunk runner processes at most one historical
                # terminal game and checkpoints before releasing this claim.
                "force": False,
                "cooperativeEventBridgeOwner": True,
            }
            claim_requeued = False
            chunk_stage: Optional[str] = None
            chunk_error_code: Optional[str] = None
            try:
                replay_remaining_seconds = _remaining_seconds(context)
                if (
                    replay_remaining_seconds
                    < COOPERATIVE_REPLAY_MIN_REMAINING_SECONDS
                ):
                    raise RuntimeError(
                        "MLB_COOPERATIVE_REPLAY_BUDGET_DEPLETED_BEFORE_DELEGATE"
                    )

                chunk_runner = getattr(
                    mlb_daily_pick_lock,
                    "run_cooperative_terminal_chunk",
                    None,
                )
                if (
                    not callable(chunk_runner)
                    or getattr(
                        mlb_daily_pick_lock,
                        "MLB_COOPERATIVE_TERMINAL_CHUNK_VERSION",
                        None,
                    )
                    != COOPERATIVE_TERMINAL_CHUNK_VERSION
                ):
                    chunk_stage = "VERIFY_CHUNK_RUNNER"
                    chunk_error_code = "CHUNK_RUNNER_NOT_READY"
                    raise RuntimeError(
                        "MLB_COOPERATIVE_TERMINAL_CHUNK_RUNNER_NOT_READY"
                    )
                if callable(chunk_runner):
                    chunk_result = chunk_runner(
                        slate_date=str(
                            claimed.get("slate_date_et") or ""
                        ),
                        request_epoch=claimed.get(
                            "requested_at_epoch"
                        ),
                        request_id=claimed.get("request_id"),
                        checkpoint=claimed.get(
                            "terminal_replay_progress"
                        ),
                        context=context,
                    )
                    if not isinstance(chunk_result, dict):
                        raise RuntimeError(
                            "MLB_COOPERATIVE_TERMINAL_CHUNK_RESULT_INVALID"
                        )
                    if "_completionLease" in chunk_result:
                        candidate_completion_lease = chunk_result.get(
                            "_completionLease"
                        )
                        if not isinstance(
                            candidate_completion_lease, dict
                        ):
                            raise RuntimeError(
                                "MLB_COOPERATIVE_TERMINAL_"
                                "COMPLETION_LEASE_INVALID"
                            )
                        retained_completion_lease = (
                            candidate_completion_lease
                        )
                    chunk_stage = str(chunk_result.get("stage") or "")[:160]
                    chunk_error_code = str(
                        chunk_result.get("errorCode") or ""
                    )[:160]
                    if (
                        chunk_result.get("terminalChunkVersion")
                        != COOPERATIVE_TERMINAL_CHUNK_VERSION
                        or chunk_result.get(
                            "postStartPredictionCreationAllowed"
                        )
                        is not False
                        or chunk_result.get(
                            "immutablePredictionRewriteAllowed"
                        )
                        is not False
                        or chunk_result.get("productionAuthorityChanged")
                        is not False
                    ):
                        raise RuntimeError(
                            "MLB_COOPERATIVE_TERMINAL_CHUNK_CONTRACT_INVALID"
                        )

                    if chunk_result.get("complete") is True:
                        if (
                            chunk_result.get("ok") is not True
                            or retained_completion_lease is None
                        ):
                            raise RuntimeError(
                                "MLB_COOPERATIVE_TERMINAL_CHUNK_COMPLETE_UNHEALTHY"
                            )
                        validator = getattr(
                            mlb_daily_pick_lock,
                            "validate_cooperative_terminal_completion_handoff",
                            None,
                        )
                        releaser = getattr(
                            mlb_daily_pick_lock,
                            "release_cooperative_terminal_completion_lease",
                            None,
                        )
                        if (
                            not callable(validator)
                            or not callable(releaser)
                            or getattr(
                                mlb_daily_pick_lock,
                                "MLB_COOPERATIVE_TERMINAL_"
                                "COMPLETION_HANDOFF_VERSION",
                                None,
                            )
                            != COOPERATIVE_TERMINAL_COMPLETION_HANDOFF_VERSION
                        ):
                            raise RuntimeError(
                                "MLB_COOPERATIVE_TERMINAL_"
                                "COMPLETION_HANDOFF_NOT_READY"
                            )

                        completion_item = claimed
                        expected_completion_progress = (
                            completion_item.get("terminal_replay_progress")
                        )
                        if not isinstance(
                            expected_completion_progress, dict
                        ):
                            observed_completion_item = _cooperative_record(
                                _read_cooperative_replay(),
                                str(
                                    claimed.get("slate_date_et") or ""
                                ),
                            )
                            if (
                                observed_completion_item.get("state")
                                != COOPERATIVE_REPLAY_CLAIMED
                                or observed_completion_item.get(
                                    "claim_owner"
                                )
                                != owner
                                or observed_completion_item.get(
                                    "requested_at_epoch"
                                )
                                != claimed.get("requested_at_epoch")
                                or observed_completion_item.get("request_id")
                                != claimed.get("request_id")
                                or not isinstance(
                                    observed_completion_item.get(
                                        "terminal_replay_progress"
                                    ),
                                    dict,
                                )
                            ):
                                raise RuntimeError(
                                    "MLB_COOPERATIVE_TERMINAL_"
                                    "COMPLETION_PROGRESS_NOT_CURRENT"
                                )
                            completion_item = observed_completion_item
                            expected_completion_progress = (
                                completion_item[
                                    "terminal_replay_progress"
                                ]
                            )
                        if (
                            chunk_result.get("checkpoint")
                            != expected_completion_progress
                        ):
                            raise RuntimeError(
                                "MLB_COOPERATIVE_TERMINAL_"
                                "COMPLETION_CHECKPOINT_MISMATCH"
                            )
                        handoff = validator(
                            slate_date=str(
                                completion_item.get("slate_date_et") or ""
                            ),
                            request_epoch=completion_item.get(
                                "requested_at_epoch"
                            ),
                            request_id=completion_item.get("request_id"),
                            checkpoint=expected_completion_progress,
                            chunk_result=chunk_result,
                        )
                        if (
                            not isinstance(handoff, dict)
                            or handoff.get("ok") is not True
                            or handoff.get("lease")
                            != retained_completion_lease
                            or handoff.get("ownerExposed") is not False
                        ):
                            raise RuntimeError(
                                "MLB_COOPERATIVE_TERMINAL_"
                                "COMPLETION_HANDOFF_INVALID"
                            )
                        retained_completion_lease = handoff["lease"]

                        terminal_payload = chunk_result.get(
                            "terminalReplayResponse"
                        )
                        if not isinstance(terminal_payload, dict):
                            raise RuntimeError(
                                "MLB_COOPERATIVE_TERMINAL_CHUNK_"
                                "COMPLETION_RESPONSE_INVALID"
                            )
                        # Validate and owner-fence the queue transition while
                        # the same V2 + legacy bridge mutation lease that
                        # guarded the atomic read remains live.
                        replay_response = _resp(200, terminal_payload)
                        completed = _complete_cooperative_replay(
                            item=completion_item,
                            owner=owner,
                            response=replay_response,
                        )
                        cooperative_owner_status = {
                            **completed,
                            "currentSlateRanFirst": True,
                            "currentSlateSuccessProofCarriedAcrossInvocation": (
                                current_slate_proof_carried
                            ),
                            "historicalReplayAttempted": True,
                            "historicalReplayCompleted": True,
                            "historicalReplayBoundedPostWindowRoute": True,
                            "historicalReplayChunked": True,
                            "singleGamePerEventBridgeOwner": True,
                            "terminalChunkVersion": (
                                COOPERATIVE_TERMINAL_CHUNK_VERSION
                            ),
                            "terminalChunkStage": chunk_stage,
                            "completionMutationLeaseHeldThroughQueueCas": True,
                            "historicalReplayStartedWithRemainingSeconds": (
                                replay_remaining_seconds
                            ),
                            "historicalReplayFinishedWithRemainingSeconds": (
                                chunk_result.get("remainingSeconds")
                            ),
                            "claimOwnerIsCurrentLeaseOwner": True,
                        }
                    else:
                        checkpointed: Optional[Dict[str, Any]] = None
                        progress = chunk_result.get("checkpoint")
                        candidate_review_required = bool(
                            chunk_result.get("ok") is not True
                            and _cooperative_replay_requires_review(
                                chunk_stage,
                                chunk_error_code,
                            )
                        )
                        if (
                            candidate_review_required
                            and not isinstance(progress, dict)
                        ):
                            progress = claimed.get(
                                "terminal_replay_progress"
                            )
                        if (
                            isinstance(progress, dict)
                            and (
                                chunk_result.get(
                                    "checkpointWriteAllowed"
                                )
                                is True
                                or candidate_review_required
                            )
                        ):
                            review_required = (
                                candidate_review_required
                            )
                            checkpointed = _checkpoint_cooperative_replay(
                                item=claimed,
                                owner=owner,
                                progress=progress,
                                failed=chunk_result.get("ok") is not True,
                                review_required=review_required,
                            )
                            # A REVIEW_REQUIRED CAS is a completed claim
                            # transition too; the exception path must not
                            # turn it back into a hot-looping QUEUED state.
                            claim_requeued = True
                        else:
                            claim_requeued = _requeue_cooperative_replay(
                                claimed,
                                owner,
                            )
                        if not claim_requeued:
                            raise RuntimeError(
                                "MLB_COOPERATIVE_TERMINAL_CHUNK_REQUEUE_FAILED"
                            )
                        if chunk_result.get("ok") is not True:
                            raise RuntimeError(
                                "MLB_COOPERATIVE_TERMINAL_CHUNK_FAILED_CLOSED:"
                                f"stage={chunk_stage or 'UNKNOWN'}:"
                                f"errorCode={chunk_error_code or 'UNKNOWN'}"
                            )
                        cooperative_owner_status = {
                            **(
                                checkpointed
                                or _cooperative_public_state(
                                    {
                                        **claimed,
                                        "state": COOPERATIVE_REPLAY_QUEUED,
                                    }
                                )
                            ),
                            "state": (
                                checkpointed.get("state")
                                if isinstance(checkpointed, dict)
                                else COOPERATIVE_REPLAY_QUEUED
                            ),
                            "currentSlateRanFirst": True,
                            "currentSlateSuccessProofCarriedAcrossInvocation": (
                                current_slate_proof_carried
                            ),
                            "historicalReplayAttempted": True,
                            "historicalReplayCompleted": False,
                            "historicalReplayBoundedPostWindowRoute": True,
                            "historicalReplayChunked": True,
                            "singleGamePerEventBridgeOwner": True,
                            "terminalChunkVersion": (
                                COOPERATIVE_TERMINAL_CHUNK_VERSION
                            ),
                            "terminalChunkStage": chunk_stage,
                            "terminalChunkDeferred": (
                                chunk_result.get("deferred") is True
                            ),
                            "terminalWrittenThisInvocation": (
                                chunk_result.get(
                                    "terminalWrittenThisInvocation"
                                )
                                is True
                            ),
                            "historicalReplayStartedWithRemainingSeconds": (
                                replay_remaining_seconds
                            ),
                            "historicalReplayFinishedWithRemainingSeconds": (
                                chunk_result.get("remainingSeconds")
                            ),
                            "claimOwnerIsCurrentLeaseOwner": True,
                        }
            except BaseException as exc:
                requeued = claim_requeued or _requeue_cooperative_replay(
                    claimed,
                    owner,
                )
                print(
                    json.dumps(
                        {
                            "event": (
                                "MLB_COOPERATIVE_TERMINAL_REPLAY_FAILED_"
                                "RETAINED_FAIL_CLOSED"
                            ),
                            "slateDateEt": str(
                                claimed.get("slate_date_et") or ""
                            ),
                            "requestRequeued": requeued,
                            "staleClaimReclaimable": not requeued,
                            "activeLeaseMutationAllowed": False,
                            "terminalChunkVersion": (
                                COOPERATIVE_TERMINAL_CHUNK_VERSION
                            ),
                            "terminalChunkStage": chunk_stage,
                            "terminalChunkErrorCode": (
                                chunk_error_code or _error_code(exc)
                            ),
                        },
                        sort_keys=True,
                    )
                )
                raise
    except BaseException as exc:
        primary_error = exc
    finally:
        if retained_completion_lease is not None:
            try:
                completion_releaser = getattr(
                    mlb_daily_pick_lock,
                    "release_cooperative_terminal_completion_lease",
                    None,
                )
                if not callable(completion_releaser):
                    raise RuntimeError(
                        "MLB_COOPERATIVE_TERMINAL_"
                        "COMPLETION_LEASE_RELEASE_NOT_READY"
                    )
                completion_release = completion_releaser(
                    slate_date=str(
                        retained_completion_lease.get("slateDateEt") or ""
                    ),
                    lease=retained_completion_lease,
                )
                if (
                    not isinstance(completion_release, dict)
                    or completion_release.get("released") is not True
                    or completion_release.get("ownerExposed") is not False
                ):
                    raise RuntimeError(
                        "MLB_COOPERATIVE_TERMINAL_"
                        "COMPLETION_LEASE_RELEASE_INVALID"
                    )
                retained_completion_lease = None
            except BaseException as exc:
                completion_lease_release_error = exc
                print(
                    json.dumps(
                        {
                            "event": (
                                "MLB_COOPERATIVE_TERMINAL_"
                                "COMPLETION_LEASE_RELEASE_FAILED"
                            ),
                            "errorCode": _error_code(exc),
                            "ownerExposed": False,
                        },
                        sort_keys=True,
                    )
                )
                if primary_error is None:
                    primary_error = RuntimeError(
                        "MLB_COOPERATIVE_TERMINAL_"
                        "COMPLETION_LEASE_RELEASE_FAILED"
                    )
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
    response = _attach_cooperative_owner_status(
        response,
        cooperative_owner_status,
    )
    return response
