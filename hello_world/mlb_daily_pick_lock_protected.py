from __future__ import annotations

import copy
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
COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_EVENT_FLAG = (
    "requeueSourcePullProofReviewAfterRebind"
)
COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_EVENT_KEYS = frozenset(
    {
        "sport",
        "run",
        "slateDateEt",
        "force",
        COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_EVENT_FLAG,
    }
)
COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_REMEDIATION_VERSION = (
    "MLB-COOPERATIVE-SOURCE-PULL-REBIND-REVIEW-REMEDIATION-v1-one-shot"
)
COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_REMEDIATION_FIELD = (
    "source_pull_rebind_review_remediation"
)
COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_HISTORY_FIELD = (
    "source_pull_rebind_review_remediation_history"
)
COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_HISTORY_VERSION = (
    "MLB-COOPERATIVE-SOURCE-PULL-REBIND-REVIEW-HISTORY-"
    "v1-compact-acknowledged"
)
COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_HISTORY_MAX_BYTES = 2048
COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_SLATE_DATE = "2026-08-04"
COOPERATIVE_SOURCE_PULL_REBIND_REQUIRED_VERSION = (
    "MLB-STATUS-SOURCE-PULL-REBIND-v1-strong-immutable-row"
)
COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_ERROR = (
    "VALID_PRELOCK_QUARANTINE_SOURCE_PULL_PROOF_MISMATCH"
)
COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_STAGE = (
    "WRITE_VALID_PRELOCK_MISSED_LOCK_QUARANTINE"
)
COOPERATIVE_TERMINAL_REVIEW_CHECKPOINT_GUARD_VERSION = (
    "MLB-COOPERATIVE-TERMINAL-REVIEW-CHECKPOINT-GUARD-"
    "v1-fresh-failed-checkpoint"
)
COOPERATIVE_TERMINAL_REVIEW_EVIDENCE_VERSION = (
    "MLB-COOPERATIVE-TERMINAL-REVIEW-EVIDENCE-"
    "v1-claimed-runtime-result"
)
COOPERATIVE_TERMINAL_REVIEW_EVIDENCE_FIELD = (
    "terminal_replay_review_evidence"
)
COOPERATIVE_TERMINAL_REVIEW_EVIDENCE_MAX_BYTES = 2048
COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_EVENT_FLAG = (
    "requeuePrelockCandidateReviewAfterInstalledRuntimeProofV2"
)
COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_EVENT_KEYS = frozenset(
    {
        "sport",
        "run",
        "slateDateEt",
        "force",
        COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_EVENT_FLAG,
    }
)
COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_REMEDIATION_VERSION = (
    "MLB-COOPERATIVE-PRELOCK-CANDIDATE-REVIEW-REMEDIATION-"
    "v2-one-shot-installed-runtime-proof"
)
COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_PROOF_VERSION = (
    "MLB-COOPERATIVE-PRELOCK-CANDIDATE-REVIEW-PROOF-"
    "v1-strong-installed-runtime"
)
COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_FIELD = (
    "prelock_candidate_review_remediation_v2"
)
COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_HISTORY_FIELD = (
    "prelock_candidate_review_remediation_v2_history"
)
COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_HISTORY_VERSION = (
    "MLB-COOPERATIVE-PRELOCK-CANDIDATE-REVIEW-HISTORY-"
    "v1-compact-acknowledged"
)
COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_HISTORY_MAX_BYTES = 3072
COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_SLATE = "2026-08-04"
COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_MANIFEST_COUNT = 15
COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_GAME_INDEX = 1
COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_PRIOR_GAME_INDEX = 0
COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_ATTEMPT_COUNT = 1007
COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_BOUND_PULL_COUNT = 68
COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_GAME_IDENTITY_FINGERPRINT = (
    "8b80aefe782634bd4a89bd51c6a043d8687ad625085c79a910f6c0bde68c3242"
)
COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_REASON = (
    "PRELOCK_CANDIDATE_REQUIRES_REVIEW"
)
COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_STALE_STAGE = (
    "PROCESS_CHECKPOINT_READY"
)
COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_STALE_STATUS = (
    "TERMINAL_CHECKPOINT_READY"
)
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
COOPERATIVE_TERMINAL_PUBLIC_MAX_MANIFEST_GAMES = 15
COOPERATIVE_TERMINAL_PUBLIC_MAX_ATTEMPTS = 1_000_000
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
_expected_status_source_pull_rebind_version = getattr(
    mlb_daily_per_game_lock_patch,
    "STATUS_SOURCE_PULL_REBIND_VERSION",
    None,
)
_status_source_pull_rebind_version = getattr(
    mlb_daily_pick_lock,
    "MLB_STATUS_SOURCE_PULL_REBIND_VERSION",
    None,
)
_status_source_pull_rebind_ready = bool(
    _expected_status_source_pull_rebind_version
    == COOPERATIVE_SOURCE_PULL_REBIND_REQUIRED_VERSION
    and _status_source_pull_rebind_version
    == COOPERATIVE_SOURCE_PULL_REBIND_REQUIRED_VERSION
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
    "sourcePullRebindReviewRemediation": {
        "version": (
            COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_REMEDIATION_VERSION
        ),
        "incidentSlateDate": (
            COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_SLATE_DATE
        ),
        "requiredSourcePullRebindVersion": (
            COOPERATIVE_SOURCE_PULL_REBIND_REQUIRED_VERSION
        ),
        "installedSourcePullRebindVersion": (
            _status_source_pull_rebind_version
        ),
        "ready": _status_source_pull_rebind_ready,
        "oneShot": True,
        "durableIncidentHistory": True,
        "durableIncidentHistoryMaximumEntries": 1,
        "durableIncidentHistoryMaximumBytes": (
            COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_HISTORY_MAX_BYTES
        ),
        "automaticPollRequeueAllowed": False,
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

    invalid = {
        "version": COOPERATIVE_TERMINAL_CHUNK_VERSION,
        "valid": False,
        "failClosed": True,
    }

    def strict_integer(value: Any, *, maximum: int) -> Optional[int]:
        if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
            return None
        if isinstance(value, int):
            return value if 0 <= value <= maximum else None
        if (
            not value.is_finite()
            or value < 0
            or value > maximum
            or value != value.to_integral_value()
        ):
            return None
        return int(value)

    field_maximums = {
        "manifestGameCount": COOPERATIVE_TERMINAL_PUBLIC_MAX_MANIFEST_GAMES,
        "nextGameIndex": COOPERATIVE_TERMINAL_PUBLIC_MAX_MANIFEST_GAMES,
        "processedGameCount": COOPERATIVE_TERMINAL_PUBLIC_MAX_MANIFEST_GAMES,
        "terminalCount": COOPERATIVE_TERMINAL_PUBLIC_MAX_MANIFEST_GAMES,
        "canonicalCount": COOPERATIVE_TERMINAL_PUBLIC_MAX_MANIFEST_GAMES,
        "noPredictionDataCount": (
            COOPERATIVE_TERMINAL_PUBLIC_MAX_MANIFEST_GAMES
        ),
        "missedLockValidPrelockQuarantineCount": (
            COOPERATIVE_TERMINAL_PUBLIC_MAX_MANIFEST_GAMES
        ),
        "reconciledCount": COOPERATIVE_TERMINAL_PUBLIC_MAX_MANIFEST_GAMES,
        "verificationIndex": COOPERATIVE_TERMINAL_PUBLIC_MAX_MANIFEST_GAMES,
        "verifiedGameCount": COOPERATIVE_TERMINAL_PUBLIC_MAX_MANIFEST_GAMES,
        "attemptCount": COOPERATIVE_TERMINAL_PUBLIC_MAX_ATTEMPTS,
    }
    values = {
        field: strict_integer(progress.get(field), maximum=maximum)
        for field, maximum in field_maximums.items()
    }
    phase = progress.get("phase")
    verification_complete = progress.get("verificationComplete")
    if (
        progress.get("version") != COOPERATIVE_TERMINAL_CHUNK_VERSION
        or progress.get("slateDateEt") != item.get("slate_date_et")
        or any(value is None for value in values.values())
        or phase not in {"PROCESS", "VERIFY"}
        or not isinstance(verification_complete, bool)
        or progress.get("postStartPredictionCreationAllowed") is not False
        or progress.get("immutablePredictionRewriteAllowed") is not False
        or progress.get("productionAuthorityChanged") is not False
    ):
        return dict(invalid)

    manifest_count = int(values["manifestGameCount"] or 0)
    next_index = int(values["nextGameIndex"] or 0)
    processed_count = int(values["processedGameCount"] or 0)
    terminal_count = int(values["terminalCount"] or 0)
    canonical_count = int(values["canonicalCount"] or 0)
    no_prediction_count = int(values["noPredictionDataCount"] or 0)
    quarantine_count = int(
        values["missedLockValidPrelockQuarantineCount"] or 0
    )
    reconciled_count = int(values["reconciledCount"] or 0)
    verification_index = int(values["verificationIndex"] or 0)
    verified_count = int(values["verifiedGameCount"] or 0)
    attempt_count = int(values["attemptCount"] or 0)
    expected_verification_complete = (
        phase == "VERIFY" and verification_index == manifest_count
    )
    if (
        manifest_count < 1
        or next_index > manifest_count
        or processed_count != next_index
        or terminal_count != next_index
        or (
            canonical_count + no_prediction_count + quarantine_count
            != terminal_count
        )
        or reconciled_count > no_prediction_count + quarantine_count
        or verification_index > manifest_count
        or verified_count != verification_index
        or attempt_count < 1
        or attempt_count < next_index + verification_index
        or (phase == "PROCESS") != (next_index < manifest_count)
        or (phase == "PROCESS" and verification_index != 0)
        or verification_complete != expected_verification_complete
    ):
        return dict(invalid)

    public = {
        "version": COOPERATIVE_TERMINAL_CHUNK_VERSION,
        "valid": True,
        **values,
        "phase": phase,
        "verificationComplete": verification_complete,
        "remainingGameCount": manifest_count - next_index,
        "remainingVerificationCount": manifest_count - verification_index,
        "oneGamePerEventBridgeOwner": True,
        "postStartPredictionCreationAllowed": False,
        "immutablePredictionRewriteAllowed": False,
        "productionAuthorityChanged": False,
    }

    if verification_complete is True:
        validator = getattr(
            mlb_daily_pick_lock,
            "validate_cooperative_terminal_completion_checkpoint",
            None,
        )
        try:
            validated = validator(progress) if callable(validator) else None
            if (
                not isinstance(validated, tuple)
                or len(validated) != 3
                or validated[0] != progress
                or not isinstance(validated[1], list)
                or len(validated[1]) > COOPERATIVE_TERMINAL_ATOMIC_MAX_ITEMS
                or re.fullmatch(
                    r"[0-9a-f]{64}",
                    str(validated[2] or ""),
                )
                is None
            ):
                raise RuntimeError(
                    "MLB_COOPERATIVE_REPLAY_COMPLETE_PROGRESS_INVALID"
                )
            terminal_games, terminal_game_set_fingerprint = (
                _checkpoint_terminal_game_receipt(progress)
            )
            authority = progress.get("manifestAuthority")
            if not isinstance(authority, Mapping):
                raise RuntimeError(
                    "MLB_COOPERATIVE_REPLAY_COMPLETE_PROGRESS_INVALID"
                )
            fingerprint_values = {
                "manifestFingerprint": str(
                    progress.get("manifestFingerprint") or ""
                ),
                "checkpointFingerprint": str(
                    progress.get("checkpointFingerprint") or ""
                ),
                "manifestAuthorityEvidenceFingerprint": str(
                    authority.get("authorityEvidenceFingerprint") or ""
                ),
                "providerManifestFingerprint": str(
                    authority.get("fingerprint") or ""
                ),
                "atomicDurableReadSetFingerprint": str(
                    validated[2] or ""
                ),
            }
        except (RuntimeError, TypeError, ValueError):
            return dict(invalid)
        if any(
            re.fullmatch(r"[0-9a-f]{64}", value) is None
            for value in fingerprint_values.values()
        ):
            return dict(invalid)
        public.update(fingerprint_values)
        public["terminalGames"] = terminal_games
        public["terminalGameSetFingerprint"] = (
            terminal_game_set_fingerprint
        )

    last_attempt = progress.get("lastAttempt")
    if (attempt_count == 0) != (last_attempt is None):
        return dict(invalid)
    if last_attempt is None:
        return public
    if not isinstance(last_attempt, dict):
        return dict(invalid)

    required_fields = {"status", "stage", "atUtc", "phase"}
    optional_fields = {
        "gameIndex",
        "gameIdentity",
        "durableIdentity",
        "errorCode",
    }
    if (
        not required_fields.issubset(last_attempt)
        or any(
            not isinstance(key, str)
            or key not in required_fields | optional_fields
            for key in last_attempt
        )
    ):
        return dict(invalid)

    status = last_attempt.get("status")
    stage = last_attempt.get("stage")
    at_utc = last_attempt.get("atUtc")
    attempt_phase = last_attempt.get("phase")
    if (
        not isinstance(status, str)
        or not isinstance(stage, str)
        or not isinstance(at_utc, str)
        or not isinstance(attempt_phase, str)
        or not status
        or not stage
        or not at_utc
        or attempt_phase != phase
        or len(at_utc) > 32
    ):
        return dict(invalid)

    try:
        parsed_at = datetime.fromisoformat(at_utc)
    except (TypeError, ValueError):
        return dict(invalid)
    if (
        parsed_at.tzinfo is None
        or parsed_at.utcoffset() != timedelta(0)
        or parsed_at.isoformat() != at_utc
    ):
        return dict(invalid)

    public_attempt: Dict[str, Any] = {
        "status": status,
        "stage": stage,
        "atUtc": at_utc,
        "phase": attempt_phase,
    }
    if "gameIndex" not in last_attempt:
        return dict(invalid)
    game_index = strict_integer(
        last_attempt.get("gameIndex"),
        maximum=manifest_count,
    )
    if game_index is None:
        return dict(invalid)
    public_attempt["gameIndex"] = game_index

    for field, maximum in (
        ("gameIdentity", 200),
        ("durableIdentity", 200),
        ("errorCode", 160),
    ):
        if field not in last_attempt:
            continue
        value = last_attempt.get(field)
        if not isinstance(value, str) or not value or len(value) > maximum:
            return dict(invalid)
        public_attempt[field] = value

    def producer_identity(value: str) -> bool:
        return any(
            value.startswith(prefix) and bool(value[len(prefix):])
            for prefix in ("provider:", "key:", "teams:")
        )

    for identity_field in ("gameIdentity", "durableIdentity"):
        identity_value = public_attempt.get(identity_field)
        if identity_value is not None and not producer_identity(
            str(identity_value)
        ):
            return dict(invalid)
    if (
        "durableIdentity" in public_attempt
        and public_attempt.get("durableIdentity")
        != public_attempt.get("gameIdentity")
    ):
        return dict(invalid)

    error_code = public_attempt.get("errorCode")

    def producer_error_code(value: str) -> bool:
        if all(
            character.isupper()
            or character.isdigit()
            or character in "_:-."
            for character in value
        ):
            return True
        prefix = stage + "_"
        if not value.startswith(prefix):
            return False
        suffix = value[len(prefix):]
        return bool(suffix and suffix.isidentifier())

    if error_code is not None and not producer_error_code(
        str(error_code)
    ):
        return dict(invalid)
    if (
        (status == "FAILED_CLOSED" and error_code is None)
        or (
            status == "DEFERRED_MUTATION_LEASE_CONTENDED"
            and error_code != "WRITER_LEASE_CONTENDED"
        )
        or (
            status
            not in {
                "FAILED_CLOSED",
                "DEFERRED_MUTATION_LEASE_CONTENDED",
            }
            and error_code is not None
        )
    ):
        return dict(invalid)

    required = "REQUIRED"
    optional = "OPTIONAL"
    forbidden = "FORBIDDEN"
    attempt_schemas = {
        (
            "TERMINAL_CHECKPOINT_READY",
            "PROCESS_CHECKPOINT_READY",
        ): (
            {
                "phases": {"PROCESS", "VERIFY"},
                "cursor": "PROCESSED_PREVIOUS",
                "gameIdentity": required,
                "durableIdentity": required,
            },
        ),
        (
            "DURABLE_TERMINAL_VERIFIED",
            "VERIFICATION_CHECKPOINT_READY",
        ): (
            {
                "phases": {"VERIFY"},
                "cursor": "VERIFIED_PREVIOUS",
                "verificationComplete": False,
                "gameIdentity": required,
                "durableIdentity": required,
            },
        ),
        (
            "DURABLE_TERMINAL_VERIFIED",
            "COMPLETE_READY",
        ): (
            {
                "phases": {"VERIFY"},
                "cursor": "VERIFIED_PREVIOUS",
                "verificationComplete": True,
                "gameIdentity": required,
                "durableIdentity": required,
            },
        ),
        (
            "DEFERRED_INSUFFICIENT_REMAINING_TIME",
            "WRITE_BUDGET",
        ): (
            {
                "phases": {"PROCESS"},
                "cursor": "CURRENT",
                "gameIdentity": required,
                "durableIdentity": forbidden,
            },
        ),
        (
            "DEFERRED_INSUFFICIENT_REMAINING_TIME",
            "GAME_BUDGET",
        ): (
            {
                "phases": {"PROCESS", "VERIFY"},
                "cursor": "CURRENT",
                "gameIdentity": required,
                "durableIdentity": forbidden,
            },
        ),
        (
            "DEFERRED_INSUFFICIENT_REMAINING_TIME",
            "ATOMIC_COMPLETION_PROOF",
        ): (
            {
                "phases": {"VERIFY"},
                "cursor": "COMPLETION",
                "gameIdentity": forbidden,
                "durableIdentity": forbidden,
            },
        ),
        (
            "DEFERRED_MUTATION_LEASE_CONTENDED",
            "MUTATION_LEASE_CONTENDED",
        ): (
            {
                "phases": {"PROCESS", "VERIFY"},
                "cursor": "CURRENT",
                "gameIdentity": required,
                "durableIdentity": forbidden,
            },
            {
                "phases": {"VERIFY"},
                "cursor": "COMPLETION",
                "gameIdentity": forbidden,
                "durableIdentity": forbidden,
            },
        ),
        ("FAILED_CLOSED", "READ_DURABLE_TERMINAL"): (
            {
                "phases": {"PROCESS"},
                "cursor": "CURRENT",
                "gameIdentity": required,
                "durableIdentity": optional,
            },
        ),
        ("FAILED_CLOSED", "VERIFY_DURABLE_TERMINAL"): (
            {
                "phases": {"VERIFY"},
                "cursor": "CURRENT",
                "gameIdentity": required,
                "durableIdentity": optional,
            },
        ),
        ("FAILED_CLOSED", "PROVE_PRELOCK_ABSENCE"): (
            {
                "phases": {"PROCESS"},
                "cursor": "CURRENT",
                "gameIdentity": required,
                "durableIdentity": forbidden,
            },
        ),
        ("FAILED_CLOSED", "BIND_MANIFEST_AUTHORITY"): (
            {
                "phases": {"PROCESS", "VERIFY"},
                "cursor": "CURRENT",
                "gameIdentity": required,
                "durableIdentity": forbidden,
            },
        ),
        ("FAILED_CLOSED", "WRITE_NO_PREDICTION_TERMINAL"): (
            {
                "phases": {"PROCESS"},
                "cursor": "CURRENT",
                "gameIdentity": required,
                "durableIdentity": required,
            },
        ),
        ("FAILED_CLOSED", "READBACK_NO_PREDICTION_TERMINAL"): (
            {
                "phases": {"PROCESS"},
                "cursor": "CURRENT",
                "gameIdentity": required,
                "durableIdentity": required,
            },
        ),
        (
            "FAILED_CLOSED",
            "WRITE_VALID_PRELOCK_MISSED_LOCK_QUARANTINE",
        ): (
            {
                "phases": {"PROCESS"},
                "cursor": "CURRENT",
                "gameIdentity": required,
                "durableIdentity": required,
            },
        ),
        (
            "FAILED_CLOSED",
            "READBACK_VALID_PRELOCK_QUARANTINE_TERMINAL",
        ): (
            {
                "phases": {"PROCESS"},
                "cursor": "CURRENT",
                "gameIdentity": required,
                "durableIdentity": required,
            },
        ),
        ("FAILED_CLOSED", "VERIFY_GAME_STARTED"): (
            {
                "phases": {"PROCESS"},
                "cursor": "CURRENT",
                "gameIdentity": required,
                "durableIdentity": forbidden,
            },
        ),
        ("FAILED_CLOSED", "ACQUIRE_MUTATION_LEASE"): (
            {
                "phases": {"PROCESS", "VERIFY"},
                "cursor": "CURRENT",
                "gameIdentity": required,
                "durableIdentity": forbidden,
            },
        ),
        ("FAILED_CLOSED", "ATOMIC_COMPLETION_PROOF"): (
            {
                "phases": {"VERIFY"},
                "cursor": "COMPLETION",
                "gameIdentity": forbidden,
                "durableIdentity": forbidden,
            },
        ),
        ("FAILED_CLOSED", "RELEASE_MUTATION_LEASE"): (
            {
                "phases": {"PROCESS", "VERIFY"},
                "cursor": "CURRENT",
                "gameIdentity": required,
                "durableIdentity": forbidden,
            },
            {
                "phases": {"VERIFY"},
                "cursor": "COMPLETION",
                "gameIdentity": forbidden,
                "durableIdentity": forbidden,
            },
        ),
    }

    def cursor_matches(cursor: str) -> bool:
        if cursor == "CURRENT":
            if game_index >= manifest_count:
                return False
            expected = (
                next_index
                if phase == "PROCESS"
                else verification_index
            )
            return (
                game_index == expected
                and (
                    phase != "VERIFY"
                    or verification_complete is False
                )
            )
        if cursor == "PROCESSED_PREVIOUS":
            return (
                game_index < manifest_count
                and next_index >= 1
                and game_index == next_index - 1
                and (
                    phase == "PROCESS"
                    or (
                        phase == "VERIFY"
                        and verification_index == 0
                        and verification_complete is False
                    )
                )
            )
        if cursor == "VERIFIED_PREVIOUS":
            return (
                phase == "VERIFY"
                and verification_index >= 1
                and game_index == verification_index - 1
                and game_index < manifest_count
            )
        if cursor == "COMPLETION":
            return (
                phase == "VERIFY"
                and next_index == manifest_count
                and verification_index == manifest_count
                and verification_complete is True
                and game_index == manifest_count
            )
        return False

    def field_matches(field: str, rule: str) -> bool:
        present = field in public_attempt
        if rule == required:
            return present
        if rule == forbidden:
            return not present
        return rule == optional

    schemas = attempt_schemas.get((status, stage), ())
    schema_valid = False
    for schema in schemas:
        expected_complete = schema.get("verificationComplete")
        if (
            phase not in schema["phases"]
            or (
                expected_complete is not None
                and verification_complete is not expected_complete
            )
            or not cursor_matches(str(schema["cursor"]))
            or not field_matches(
                "gameIdentity",
                str(schema["gameIdentity"]),
            )
            or not field_matches(
                "durableIdentity",
                str(schema["durableIdentity"]),
            )
        ):
            continue
        schema_valid = True
        break
    if not schema_valid:
        return dict(invalid)

    public["lastAttempt"] = public_attempt
    return public


def _cooperative_review_reason(item: Dict[str, Any]) -> str:
    evidence = item.get(COOPERATIVE_TERMINAL_REVIEW_EVIDENCE_FIELD)
    if evidence is not None:
        validated_evidence = _validated_cooperative_review_evidence(
            evidence,
            item,
        )
        return str(validated_evidence["errorCode"])

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


def _source_pull_rebind_checkpoint_fingerprint(
    checkpoint: Dict[str, Any],
) -> str:
    """Recompute the producer's request-bound safety fingerprint exactly."""

    def ddb_number_normalized(value: Any) -> Any:
        if isinstance(value, Decimal):
            if value.is_finite() and value == value.to_integral_value():
                return int(value)
            return str(value)
        if isinstance(value, dict):
            return {
                str(key): ddb_number_normalized(child)
                for key, child in value.items()
            }
        if isinstance(value, list):
            return [ddb_number_normalized(child) for child in value]
        return value

    material = {
        str(key): ddb_number_normalized(value)
        for key, value in checkpoint.items()
        if key
        not in {
            "checkpointFingerprint",
            "attemptCount",
            "lastAttempt",
            "updatedAtUtc",
        }
    }
    return hashlib.sha256(
        json.dumps(
            material,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _validated_source_pull_rebind_remediation_marker(
    marker: Any,
    item: Dict[str, Any],
) -> Dict[str, Any]:
    expected_keys = {
        "version",
        "sourcePullRebindVersion",
        "slateDateEt",
        "requestEpoch",
        "requestIdFingerprint",
        "checkpointFingerprint",
        "errorCode",
        "stage",
        "appliedAtUtc",
        "appliedAtEpoch",
        "oneShot",
    }
    if not isinstance(marker, dict) or set(marker) != expected_keys:
        raise RuntimeError(
            "MLB_COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_REMEDIATION_INVALID"
        )
    try:
        applied_at_text = str(marker.get("appliedAtUtc") or "")
        applied_at = datetime.fromisoformat(applied_at_text)
        applied_epoch_number = Decimal(str(marker.get("appliedAtEpoch")))
        marker_request_epoch_number = Decimal(
            str(marker.get("requestEpoch"))
        )
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise RuntimeError(
            "MLB_COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_REMEDIATION_INVALID"
        ) from exc
    request_epoch = int(item["requested_at_epoch"])
    request_id = str(item.get("request_id") or "")
    request_id_fingerprint = hashlib.sha256(
        request_id.encode("utf-8")
    ).hexdigest()
    if (
        isinstance(marker.get("appliedAtEpoch"), bool)
        or isinstance(marker.get("requestEpoch"), bool)
        or not applied_epoch_number.is_finite()
        or applied_epoch_number <= 0
        or applied_epoch_number != applied_epoch_number.to_integral_value()
        or not marker_request_epoch_number.is_finite()
        or marker_request_epoch_number != request_epoch
        or marker_request_epoch_number
        != marker_request_epoch_number.to_integral_value()
        or applied_at.tzinfo is None
        or applied_at.utcoffset() != timedelta(0)
        or applied_at.isoformat() != applied_at_text
        or marker.get("version")
        != COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_REMEDIATION_VERSION
        or marker.get("sourcePullRebindVersion")
        != COOPERATIVE_SOURCE_PULL_REBIND_REQUIRED_VERSION
        or str(marker.get("slateDateEt") or "")
        != str(item.get("slate_date_et") or "")
        or not request_id
        or marker.get("requestIdFingerprint")
        != request_id_fingerprint
        or re.fullmatch(
            r"[0-9a-f]{64}",
            str(marker.get("checkpointFingerprint") or ""),
        )
        is None
        or marker.get("errorCode")
        != COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_ERROR
        or marker.get("stage")
        != COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_STAGE
        or marker.get("oneShot") is not True
        or int(applied_at.timestamp()) != int(applied_epoch_number)
    ):
        raise RuntimeError(
            "MLB_COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_REMEDIATION_INVALID"
        )
    return copy.deepcopy(marker)


def _source_pull_rebind_compact_fingerprint(value: Any) -> str:
    """Fingerprint compact durable proof material with DDB number parity."""

    def ddb_number_normalized(child: Any) -> Any:
        if isinstance(child, Decimal):
            if child.is_finite() and child == child.to_integral_value():
                return int(child)
            return str(child)
        if isinstance(child, dict):
            return {
                str(key): ddb_number_normalized(nested)
                for key, nested in child.items()
            }
        if isinstance(child, list):
            return [ddb_number_normalized(nested) for nested in child]
        return child

    return hashlib.sha256(
        json.dumps(
            ddb_number_normalized(value),
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _validated_cooperative_review_evidence(
    value: Any,
    item: Dict[str, Any],
    *,
    owner: Optional[str] = None,
) -> Dict[str, Any]:
    """Validate a review transition that has no writable chunk checkpoint."""

    expected_keys = {
        "version",
        "slateDateEt",
        "requestEpoch",
        "requestIdFingerprint",
        "priorProgressPresent",
        "priorProgressFingerprint",
        "stage",
        "errorCode",
        "status",
        "chunkVersion",
        "claimAcquiredAtUtc",
        "claimAcquiredAtEpoch",
        "claimOwnerFingerprint",
        "recordedAtUtc",
        "recordedAtEpoch",
        "postStartPredictionCreationAllowed",
        "immutablePredictionRewriteAllowed",
        "directWorkflowTableWrite",
        "productionAuthorityChanged",
        "evidenceFingerprint",
    }
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise RuntimeError(
            "MLB_COOPERATIVE_TERMINAL_REVIEW_EVIDENCE_INVALID"
        )
    prior_present = "terminal_replay_progress" in item
    prior = item.get("terminal_replay_progress")
    if prior_present and not isinstance(prior, dict):
        raise RuntimeError(
            "MLB_COOPERATIVE_TERMINAL_REVIEW_EVIDENCE_INVALID"
        )
    request_id = str(item.get("request_id") or "")
    claim_at_text = str(value.get("claimAcquiredAtUtc") or "")
    recorded_at_text = str(value.get("recordedAtUtc") or "")
    try:
        request_epoch = _nonnegative_receipt_integer(
            item.get("requested_at_epoch"),
            "review_evidence_request_epoch",
        )
        bound_request_epoch = _nonnegative_receipt_integer(
            value.get("requestEpoch"),
            "review_evidence_bound_request_epoch",
        )
        claim_epoch = _nonnegative_receipt_integer(
            value.get("claimAcquiredAtEpoch"),
            "review_evidence_claim_epoch",
        )
        recorded_epoch = _nonnegative_receipt_integer(
            value.get("recordedAtEpoch"),
            "review_evidence_recorded_epoch",
        )
        claim_at = datetime.fromisoformat(claim_at_text)
        recorded_at = datetime.fromisoformat(recorded_at_text)
    except (RuntimeError, TypeError, ValueError) as exc:
        raise RuntimeError(
            "MLB_COOPERATIVE_TERMINAL_REVIEW_EVIDENCE_INVALID"
        ) from exc

    stage = str(value.get("stage") or "")
    error_code = str(value.get("errorCode") or "")
    expected_prior_fingerprint = (
        _source_pull_rebind_compact_fingerprint(prior)
        if isinstance(prior, dict)
        else None
    )
    material = {
        key: child
        for key, child in value.items()
        if key != "evidenceFingerprint"
    }
    encoded_size = len(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    )
    owner_matches = True
    if owner is not None:
        owner_value = str(owner or "")
        owner_matches = bool(
            owner_value
            and str(item.get("claim_owner") or "") == owner_value
            and value.get("claimOwnerFingerprint")
            == hashlib.sha256(owner_value.encode("utf-8")).hexdigest()
            and value.get("claimAcquiredAtUtc")
            == item.get("claim_acquired_at_utc")
            and claim_epoch
            == _nonnegative_receipt_integer(
                item.get("claim_acquired_at_epoch"),
                "review_evidence_item_claim_epoch",
            )
        )
    fingerprint_fields = (
        "requestIdFingerprint",
        "claimOwnerFingerprint",
        "evidenceFingerprint",
    )
    if (
        not request_id
        or request_epoch <= 0
        or bound_request_epoch != request_epoch
        or str(value.get("slateDateEt") or "")
        != str(item.get("slate_date_et") or "")
        or value.get("requestIdFingerprint")
        != hashlib.sha256(request_id.encode("utf-8")).hexdigest()
        or value.get("version")
        != COOPERATIVE_TERMINAL_REVIEW_EVIDENCE_VERSION
        or value.get("priorProgressPresent") is not prior_present
        or value.get("priorProgressFingerprint")
        != expected_prior_fingerprint
        or not _cooperative_replay_requires_review(stage, error_code)
        or value.get("status") != "FAILED_CLOSED"
        or value.get("chunkVersion")
        != COOPERATIVE_TERMINAL_CHUNK_VERSION
        or claim_at.tzinfo is None
        or claim_at.utcoffset() != timedelta(0)
        or claim_at.isoformat() != claim_at_text
        or int(claim_at.timestamp()) != claim_epoch
        or recorded_at.tzinfo is None
        or recorded_at.utcoffset() != timedelta(0)
        or recorded_at.isoformat() != recorded_at_text
        or int(recorded_at.timestamp()) != recorded_epoch
        or recorded_at < claim_at
        or value.get("postStartPredictionCreationAllowed") is not False
        or value.get("immutablePredictionRewriteAllowed") is not False
        or value.get("directWorkflowTableWrite") is not False
        or value.get("productionAuthorityChanged") is not False
        or not owner_matches
        or any(
            re.fullmatch(r"[0-9a-f]{64}", str(value.get(field) or ""))
            is None
            for field in fingerprint_fields
        )
        or (
            prior_present
            and re.fullmatch(
                r"[0-9a-f]{64}",
                str(value.get("priorProgressFingerprint") or ""),
            )
            is None
        )
        or value.get("evidenceFingerprint")
        != _source_pull_rebind_compact_fingerprint(material)
        or encoded_size > COOPERATIVE_TERMINAL_REVIEW_EVIDENCE_MAX_BYTES
    ):
        raise RuntimeError(
            "MLB_COOPERATIVE_TERMINAL_REVIEW_EVIDENCE_INVALID"
        )
    return copy.deepcopy(value)


def _cooperative_review_evidence_from_claimed_result(
    item: Dict[str, Any],
    owner: str,
    chunk_result: Any,
) -> Dict[str, Any]:
    """Bind one in-process permanent failure to the exact claimed row."""

    stage = (
        str(chunk_result.get("stage") or "")
        if isinstance(chunk_result, dict)
        else ""
    )
    error_code = (
        str(chunk_result.get("errorCode") or "")
        if isinstance(chunk_result, dict)
        else ""
    )
    if (
        not isinstance(chunk_result, dict)
        or chunk_result.get("ok") is not False
        or chunk_result.get("complete") is not False
        or chunk_result.get("deferred") is not False
        or chunk_result.get("checkpoint") is not None
        or chunk_result.get("checkpointWriteAllowed") is not False
        or chunk_result.get("terminalChunkVersion")
        != COOPERATIVE_TERMINAL_CHUNK_VERSION
        or chunk_result.get("postStartPredictionCreationAllowed") is not False
        or chunk_result.get("immutablePredictionRewriteAllowed") is not False
        or chunk_result.get("productionAuthorityChanged") is not False
        or not _cooperative_replay_requires_review(stage, error_code)
    ):
        raise RuntimeError(
            "MLB_COOPERATIVE_TERMINAL_REVIEW_EVIDENCE_INVALID"
        )
    if (
        item.get("state") != COOPERATIVE_REPLAY_CLAIMED
        or str(item.get("claim_owner") or "") != str(owner or "")
    ):
        raise RuntimeError(
            "MLB_COOPERATIVE_TERMINAL_REVIEW_EVIDENCE_INVALID"
        )
    prior_present = "terminal_replay_progress" in item
    prior = item.get("terminal_replay_progress")
    if prior_present and not isinstance(prior, dict):
        raise RuntimeError(
            "MLB_COOPERATIVE_TERMINAL_REVIEW_EVIDENCE_INVALID"
        )
    request_epoch = _nonnegative_receipt_integer(
        item.get("requested_at_epoch"),
        "review_evidence_request_epoch",
    )
    claim_epoch = _nonnegative_receipt_integer(
        item.get("claim_acquired_at_epoch"),
        "review_evidence_claim_epoch",
    )
    request_id = str(item.get("request_id") or "")
    owner_value = str(owner or "")
    claim_at_utc = str(item.get("claim_acquired_at_utc") or "")
    now = _utc_now()
    evidence = {
        "version": COOPERATIVE_TERMINAL_REVIEW_EVIDENCE_VERSION,
        "slateDateEt": str(item.get("slate_date_et") or ""),
        "requestEpoch": request_epoch,
        "requestIdFingerprint": hashlib.sha256(
            request_id.encode("utf-8")
        ).hexdigest(),
        "priorProgressPresent": prior_present,
        "priorProgressFingerprint": (
            _source_pull_rebind_compact_fingerprint(prior)
            if isinstance(prior, dict)
            else None
        ),
        "stage": stage,
        "errorCode": error_code,
        "status": "FAILED_CLOSED",
        "chunkVersion": COOPERATIVE_TERMINAL_CHUNK_VERSION,
        "claimAcquiredAtUtc": claim_at_utc,
        "claimAcquiredAtEpoch": claim_epoch,
        "claimOwnerFingerprint": hashlib.sha256(
            owner_value.encode("utf-8")
        ).hexdigest(),
        "recordedAtUtc": now.isoformat(),
        "recordedAtEpoch": int(now.timestamp()),
        "postStartPredictionCreationAllowed": False,
        "immutablePredictionRewriteAllowed": False,
        "directWorkflowTableWrite": False,
        "productionAuthorityChanged": False,
    }
    evidence["evidenceFingerprint"] = (
        _source_pull_rebind_compact_fingerprint(evidence)
    )
    return _validated_cooperative_review_evidence(
        evidence,
        item,
        owner=owner_value,
    )


def _review_cooperative_replay_without_checkpoint(
    *,
    item: Dict[str, Any],
    owner: str,
    chunk_result: Dict[str, Any],
) -> Dict[str, Any]:
    """Persist a request-bound review outcome while preserving prior progress."""

    evidence = _cooperative_review_evidence_from_claimed_result(
        item,
        owner,
        chunk_result,
    )
    slate_date = str(item.get("slate_date_et") or "")
    request_id = str(item.get("request_id") or "")
    request_epoch = _nonnegative_receipt_integer(
        item.get("requested_at_epoch"),
        "review_evidence_request_epoch",
    )
    prior_present = "terminal_replay_progress" in item
    prior = item.get("terminal_replay_progress")
    expression_values = {
        ":record_type": COOPERATIVE_TERMINAL_REPLAY_RECORD_TYPE,
        ":version": COOPERATIVE_TERMINAL_REPLAY_VERSION,
        ":slate_date": slate_date,
        ":request_epoch": request_epoch,
        ":request_id": request_id,
        ":claimed": COOPERATIVE_REPLAY_CLAIMED,
        ":review_required": COOPERATIVE_REPLAY_REVIEW_REQUIRED,
        ":owner": owner,
        ":evidence": evidence,
        ":now_utc": evidence["recordedAtUtc"],
        ":now_epoch": evidence["recordedAtEpoch"],
        ":chunk_stage": evidence["stage"],
        ":chunk_status": evidence["status"],
    }
    if prior_present:
        expression_values[":prior_progress"] = prior
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
                    if prior_present
                    else "attribute_not_exists(terminal_replay_progress) AND "
                )
                + "#state = :claimed AND claim_owner = :owner AND "
                "attribute_not_exists(#review_evidence)"
            ),
            UpdateExpression=(
                "SET #state = :review_required, "
                "#review_evidence = :evidence, "
                "last_chunk_at_utc = :now_utc, "
                "last_chunk_at_epoch = :now_epoch, "
                "last_chunk_stage = :chunk_stage, "
                "last_chunk_status = :chunk_status, "
                "last_failure_at_utc = :now_utc, "
                "last_failure_at_epoch = :now_epoch "
                "REMOVE current_slate_success_proof, claim_owner, "
                "claim_acquired_at_utc, claim_acquired_at_epoch, "
                "claim_expires_at_utc, claim_expires_at_epoch"
            ),
            ExpressionAttributeNames={
                "#state": "state",
                "#review_evidence": (
                    COOPERATIVE_TERMINAL_REVIEW_EVIDENCE_FIELD
                ),
            },
            ExpressionAttributeValues=expression_values,
            ReturnValues="ALL_NEW",
        ).get("Attributes")
    except BaseException as exc:
        observed = _cooperative_record(
            _read_cooperative_replay(),
            slate_date,
        )
        observed_prior_present = "terminal_replay_progress" in observed
        if (
            observed.get("state") == COOPERATIVE_REPLAY_REVIEW_REQUIRED
            and observed.get("requested_at_epoch") == request_epoch
            and str(observed.get("request_id") or "") == request_id
            and observed_prior_present is prior_present
            and (
                not prior_present
                or observed.get("terminal_replay_progress") == prior
            )
            and observed.get(
                COOPERATIVE_TERMINAL_REVIEW_EVIDENCE_FIELD
            )
            == evidence
        ):
            _validated_cooperative_review_evidence(evidence, observed)
            updated = observed
        else:
            raise RuntimeError(
                "MLB_COOPERATIVE_TERMINAL_REVIEW_EVIDENCE_WRITE_FAILED"
            ) from exc

    reviewed = _cooperative_record(dict(updated or {}), slate_date)
    reviewed_prior_present = "terminal_replay_progress" in reviewed
    if (
        reviewed.get("state") != COOPERATIVE_REPLAY_REVIEW_REQUIRED
        or reviewed.get("requested_at_epoch") != request_epoch
        or str(reviewed.get("request_id") or "") != request_id
        or reviewed_prior_present is not prior_present
        or (
            prior_present
            and reviewed.get("terminal_replay_progress") != prior
        )
        or reviewed.get(COOPERATIVE_TERMINAL_REVIEW_EVIDENCE_FIELD)
        != evidence
    ):
        raise RuntimeError(
            "MLB_COOPERATIVE_TERMINAL_REVIEW_EVIDENCE_STATE_INVALID"
        )
    _validated_cooperative_review_evidence(evidence, reviewed)
    return _cooperative_public_state(reviewed)


def _validated_source_pull_rebind_remediation_history(
    value: Any,
) -> Dict[str, Any]:
    """Validate the single compact incident proof carried between queue rows."""

    expected_keys = {
        "version",
        "remediationVersion",
        "sourcePullRebindVersion",
        "slateDateEt",
        "requestEpoch",
        "requestIdFingerprint",
        "reviewCheckpointFingerprint",
        "completionCheckpointFingerprint",
        "completionReceiptFingerprint",
        "errorCode",
        "stage",
        "acknowledgedAtUtc",
        "acknowledgedAtEpoch",
        "state",
        "oneShot",
        "proofFingerprint",
    }
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise RuntimeError(
            "MLB_COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_HISTORY_INVALID"
        )
    try:
        request_epoch_number = Decimal(str(value.get("requestEpoch")))
        acknowledged_epoch_number = Decimal(
            str(value.get("acknowledgedAtEpoch"))
        )
        acknowledged_at_text = str(value.get("acknowledgedAtUtc") or "")
        acknowledged_at = datetime.fromisoformat(acknowledged_at_text)
        parsed_slate = datetime.strptime(
            str(value.get("slateDateEt") or ""),
            "%Y-%m-%d",
        ).date()
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise RuntimeError(
            "MLB_COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_HISTORY_INVALID"
        ) from exc

    fingerprints = (
        "requestIdFingerprint",
        "reviewCheckpointFingerprint",
        "completionCheckpointFingerprint",
        "completionReceiptFingerprint",
        "proofFingerprint",
    )
    proof_material = {
        key: child
        for key, child in value.items()
        if key != "proofFingerprint"
    }
    encoded_size = len(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    )
    if (
        isinstance(value.get("requestEpoch"), bool)
        or isinstance(value.get("acknowledgedAtEpoch"), bool)
        or not request_epoch_number.is_finite()
        or request_epoch_number <= 0
        or request_epoch_number != request_epoch_number.to_integral_value()
        or not acknowledged_epoch_number.is_finite()
        or acknowledged_epoch_number <= 0
        or acknowledged_epoch_number
        != acknowledged_epoch_number.to_integral_value()
        or acknowledged_at.tzinfo is None
        or acknowledged_at.utcoffset() != timedelta(0)
        or acknowledged_at.isoformat() != acknowledged_at_text
        or int(acknowledged_at.timestamp())
        != int(acknowledged_epoch_number)
        or parsed_slate.isoformat() != value.get("slateDateEt")
        or value.get("version")
        != COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_HISTORY_VERSION
        or value.get("remediationVersion")
        != COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_REMEDIATION_VERSION
        or value.get("sourcePullRebindVersion")
        != COOPERATIVE_SOURCE_PULL_REBIND_REQUIRED_VERSION
        or value.get("errorCode")
        != COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_ERROR
        or value.get("stage")
        != COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_STAGE
        or value.get("state") != COOPERATIVE_REPLAY_ACKNOWLEDGED
        or value.get("oneShot") is not True
        or any(
            re.fullmatch(r"[0-9a-f]{64}", str(value.get(field) or ""))
            is None
            for field in fingerprints
        )
        or value.get("proofFingerprint")
        != _source_pull_rebind_compact_fingerprint(proof_material)
        or encoded_size
        > COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_HISTORY_MAX_BYTES
    ):
        raise RuntimeError(
            "MLB_COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_HISTORY_INVALID"
        )
    return copy.deepcopy(value)


def _source_pull_rebind_completed_history(
    item: Dict[str, Any],
) -> Dict[str, Any]:
    """Compact one validated ACK receipt before the singleton row is reused."""

    if item.get("state") != COOPERATIVE_REPLAY_ACKNOWLEDGED:
        raise RuntimeError(
            "MLB_COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_HISTORY_NOT_ACKNOWLEDGED"
        )
    marker = _validated_source_pull_rebind_remediation_marker(
        item.get(COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_REMEDIATION_FIELD),
        item,
    )
    receipt = _validated_persisted_replay_receipt(item)
    progress = item.get("terminal_replay_progress")
    request_epoch = int(item["requested_at_epoch"])
    request_id = str(item.get("request_id") or "")
    try:
        acknowledged_at_text = str(
            item.get("acknowledged_at_utc") or ""
        )
        acknowledged_at = datetime.fromisoformat(acknowledged_at_text)
        acknowledged_epoch_number = Decimal(
            str(item.get("acknowledged_at_epoch"))
        )
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise RuntimeError(
            "MLB_COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_HISTORY_INVALID"
        ) from exc
    if (
        not isinstance(progress, dict)
        or str(progress.get("slateDateEt") or "")
        != str(item.get("slate_date_et") or "")
        or progress.get("requestEpoch") != request_epoch
        or str(progress.get("requestId") or "") != request_id
        or receipt.get("checkpointFingerprint")
        != progress.get("checkpointFingerprint")
        or isinstance(item.get("acknowledged_at_epoch"), bool)
        or not acknowledged_epoch_number.is_finite()
        or acknowledged_epoch_number <= 0
        or acknowledged_epoch_number
        != acknowledged_epoch_number.to_integral_value()
        or acknowledged_at.tzinfo is None
        or acknowledged_at.utcoffset() != timedelta(0)
        or acknowledged_at.isoformat() != acknowledged_at_text
        or int(acknowledged_at.timestamp())
        != int(acknowledged_epoch_number)
        or int(acknowledged_epoch_number)
        < int(Decimal(str(marker["appliedAtEpoch"])))
    ):
        raise RuntimeError(
            "MLB_COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_HISTORY_INVALID"
        )
    history = {
        "version": COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_HISTORY_VERSION,
        "remediationVersion": (
            COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_REMEDIATION_VERSION
        ),
        "sourcePullRebindVersion": (
            COOPERATIVE_SOURCE_PULL_REBIND_REQUIRED_VERSION
        ),
        "slateDateEt": str(item.get("slate_date_et") or ""),
        "requestEpoch": request_epoch,
        "requestIdFingerprint": hashlib.sha256(
            request_id.encode("utf-8")
        ).hexdigest(),
        "reviewCheckpointFingerprint": str(
            marker.get("checkpointFingerprint") or ""
        ),
        "completionCheckpointFingerprint": str(
            receipt.get("checkpointFingerprint") or ""
        ),
        "completionReceiptFingerprint": (
            _source_pull_rebind_compact_fingerprint(receipt)
        ),
        "errorCode": COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_ERROR,
        "stage": COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_STAGE,
        "acknowledgedAtUtc": acknowledged_at_text,
        "acknowledgedAtEpoch": int(acknowledged_epoch_number),
        "state": COOPERATIVE_REPLAY_ACKNOWLEDGED,
        "oneShot": True,
    }
    history["proofFingerprint"] = (
        _source_pull_rebind_compact_fingerprint(history)
    )
    return _validated_source_pull_rebind_remediation_history(history)


def _source_pull_rebind_history_for_replacement(
    item: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Validate and carry at most one incident proof into the next queue row."""

    # Every row eligible to replace the singleton ACK slot must still have the
    # full owner-fenced receipt that justified ACKNOWLEDGED.
    _validated_persisted_replay_receipt(item)
    raw_history = item.get(
        COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_HISTORY_FIELD
    )
    history = (
        _validated_source_pull_rebind_remediation_history(raw_history)
        if raw_history is not None
        else None
    )
    marker = item.get(
        COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_REMEDIATION_FIELD
    )
    if marker is not None:
        completed = _source_pull_rebind_completed_history(item)
        if history is not None and history != completed:
            raise RuntimeError(
                "MLB_COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_HISTORY_CONFLICT"
            )
        history = completed
    return copy.deepcopy(history) if history is not None else None


def _source_pull_rebind_history_response(
    history: Dict[str, Any],
) -> Dict[str, Any]:
    """Return a redacted ACK projection without touching the active queue row."""

    history = _validated_source_pull_rebind_remediation_history(history)
    slate_date = str(history["slateDateEt"])
    public = {
        "version": COOPERATIVE_TERMINAL_REPLAY_VERSION,
        "state": COOPERATIVE_REPLAY_ACKNOWLEDGED,
        "slateDateEt": slate_date,
        "automaticExecutionOwner": "eventbridge_daily_lock_schedule",
        "currentSlateRunsFirst": True,
        "freshPriorOwnerProofMayCarryAcrossInvocation": True,
        "currentSlateSuccessProofPresent": False,
        "activeLeaseMutationAllowed": False,
        "postStartPredictionCreationAllowed": False,
        "immutablePredictionRewriteAllowed": False,
        "directWorkflowTableWrite": False,
        "productionAuthorityChanged": False,
        "ownerIdentifierExposed": False,
        "terminalChunkProgress": None,
        "durableRemediationHistory": True,
        "failClosed": True,
    }
    status = "ACKNOWLEDGED_COMPLETION"
    return {
        "ok": True,
        "sport": "mlb",
        "slateDateEt": slate_date,
        "status": status,
        "reason": status,
        "skipped": True,
        "mutatingRunAttempted": False,
        "cooperativeTerminalReplayCompleted": True,
        "cooperativeTerminalReplay": public,
        "sourcePullRebindReviewRemediationApplied": True,
        "sourcePullRebindReviewRemediationIdempotent": True,
        "sourcePullRebindReviewRemediationVersion": (
            COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_REMEDIATION_VERSION
        ),
        "sourcePullRebindVersion": (
            COOPERATIVE_SOURCE_PULL_REBIND_REQUIRED_VERSION
        ),
        "sourcePullRebindReviewRemediationDurableHistory": True,
        "activeLeaseMutationAllowed": False,
        "postStartPredictionCreationAllowed": False,
        "immutablePredictionRewriteAllowed": False,
        "directWorkflowTableWrite": False,
        "productionAuthorityChanged": False,
    }


def _validated_source_pull_rebind_review_checkpoint(
    item: Dict[str, Any],
) -> Dict[str, Any]:
    slate_date = str(item.get("slate_date_et") or "")
    request_id = str(item.get("request_id") or "").strip()
    request_epoch = int(item["requested_at_epoch"])
    progress = item.get("terminal_replay_progress")
    last_attempt = (
        progress.get("lastAttempt")
        if isinstance(progress, dict)
        else None
    )
    public_progress = _cooperative_terminal_progress_public(item)
    if (
        not request_id
        or item.get("state") != COOPERATIVE_REPLAY_REVIEW_REQUIRED
        or _cooperative_review_reason(item)
        != COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_ERROR
        or item.get("last_chunk_stage")
        != COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_STAGE
        or item.get("last_chunk_status") != "FAILED_CLOSED"
        or not isinstance(public_progress, dict)
        or public_progress.get("valid") is not True
        or not isinstance(progress, dict)
        or progress.get("version") != COOPERATIVE_TERMINAL_CHUNK_VERSION
        or str(progress.get("slateDateEt") or "") != slate_date
        or progress.get("requestEpoch") != request_epoch
        or str(progress.get("requestId") or "") != request_id
        or str(progress.get("phase") or "") != "PROCESS"
        or re.fullmatch(
            r"[0-9a-f]{64}",
            str(progress.get("manifestFingerprint") or ""),
        )
        is None
        or not isinstance(progress.get("manifestAuthority"), Mapping)
        or progress.get("processedGames") != []
        or progress.get("verificationComplete") is not False
        or progress.get("postStartPredictionCreationAllowed") is not False
        or progress.get("immutablePredictionRewriteAllowed") is not False
        or progress.get("productionAuthorityChanged") is not False
        or not isinstance(last_attempt, dict)
        or last_attempt.get("status") != "FAILED_CLOSED"
        or last_attempt.get("stage")
        != COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_STAGE
        or last_attempt.get("phase") != "PROCESS"
        or last_attempt.get("errorCode")
        != COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_ERROR
        or not str(last_attempt.get("gameIdentity") or "").strip()
        or str(last_attempt.get("atUtc") or "")
        != str(progress.get("updatedAtUtc") or "")
    ):
        raise RuntimeError(
            "MLB_COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_CHECKPOINT_INVALID"
        )

    zero_fields = (
        "nextGameIndex",
        "processedGameCount",
        "terminalCount",
        "canonicalCount",
        "noPredictionDataCount",
        "missedLockValidPrelockQuarantineCount",
        "reconciledCount",
        "verificationIndex",
        "verifiedGameCount",
    )
    if any(
        public_progress.get(field) != 0
        for field in zero_fields
    ):
        raise RuntimeError(
            "MLB_COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_CHECKPOINT_NOT_ZERO_WORK"
        )
    if (
        public_progress.get("manifestGameCount", 0) < 1
        or public_progress.get("attemptCount", 0) < 1
        or (public_progress.get("lastAttempt") or {}).get("gameIndex")
        != 0
    ):
        raise RuntimeError(
            "MLB_COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_CHECKPOINT_INVALID"
        )
    checkpoint_fingerprint = str(
        progress.get("checkpointFingerprint") or ""
    )
    if (
        re.fullmatch(r"[0-9a-f]{64}", checkpoint_fingerprint) is None
        or checkpoint_fingerprint
        != _source_pull_rebind_checkpoint_fingerprint(progress)
    ):
        raise RuntimeError(
            "MLB_COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_CHECKPOINT_INVALID"
        )
    return copy.deepcopy(progress)


def _source_pull_rebind_review_response(
    item: Dict[str, Any],
    *,
    idempotent: bool,
) -> Dict[str, Any]:
    state = str(item.get("state") or "")
    status_by_state = {
        COOPERATIVE_REPLAY_QUEUED: "QUEUED_FOR_EVENTBRIDGE_LOCK_OWNER",
        COOPERATIVE_REPLAY_CLAIMED: "CLAIMED_BY_EVENTBRIDGE_LOCK_OWNER",
        COOPERATIVE_REPLAY_COMPLETED: "COMPLETED_BY_EVENTBRIDGE_LOCK_OWNER",
        COOPERATIVE_REPLAY_ACKNOWLEDGED: "ACKNOWLEDGED_COMPLETION",
    }
    status = status_by_state.get(state)
    if status is None:
        raise RuntimeError(
            "MLB_COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_STATE_INVALID"
        )
    completed = state in {
        COOPERATIVE_REPLAY_COMPLETED,
        COOPERATIVE_REPLAY_ACKNOWLEDGED,
    }
    if completed:
        # A marker proves only that the one-shot requeue was consumed.  Never
        # let that marker upgrade a corrupt or missing terminal receipt into a
        # completed operational proof.
        _validated_persisted_replay_receipt(item)
    return {
        "ok": True,
        "sport": "mlb",
        "slateDateEt": str(item.get("slate_date_et") or ""),
        "status": status,
        "reason": status,
        "skipped": True,
        "mutatingRunAttempted": False,
        "cooperativeTerminalReplayCompleted": completed,
        "cooperativeTerminalReplay": _cooperative_public_state(item),
        "sourcePullRebindReviewRemediationApplied": True,
        "sourcePullRebindReviewRemediationIdempotent": bool(idempotent),
        "sourcePullRebindReviewRemediationVersion": (
            COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_REMEDIATION_VERSION
        ),
        "sourcePullRebindVersion": (
            COOPERATIVE_SOURCE_PULL_REBIND_REQUIRED_VERSION
        ),
        "activeLeaseMutationAllowed": False,
        "postStartPredictionCreationAllowed": False,
        "immutablePredictionRewriteAllowed": False,
        "directWorkflowTableWrite": False,
        "productionAuthorityChanged": False,
    }


def _requeue_source_pull_proof_review_after_rebind(
    event: Dict[str, Any],
) -> Dict[str, Any]:
    slate_date = _strict_historical_slate_date(event)
    if (
        event.get(COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_EVENT_FLAG)
        is not True
        or set(event) != COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_EVENT_KEYS
        or event.get("acknowledgeCooperativeCompletion") is True
    ):
        raise RuntimeError(
            "MLB_COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_FLAG_MISSING"
        )
    if slate_date != COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_SLATE_DATE:
        raise RuntimeError(
            "MLB_COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_SLATE_INVALID"
        )
    if not _status_source_pull_rebind_ready:
        raise RuntimeError(
            "MLB_COOPERATIVE_SOURCE_PULL_REBIND_NOT_READY"
        )

    raw = _read_cooperative_replay()
    if not raw or not str(raw.get("request_id") or "").strip():
        raise RuntimeError(
            "MLB_COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_RECORD_INVALID"
        )
    current_slate_date = str(raw.get("slate_date_et") or "")
    current_item = _cooperative_record(raw, current_slate_date)
    raw_history = current_item.get(
        COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_HISTORY_FIELD
    )
    if raw_history is not None:
        history = _validated_source_pull_rebind_remediation_history(
            raw_history
        )
        if history.get("slateDateEt") == slate_date:
            if current_slate_date == slate_date:
                # A compact history is evidence for a prior consumed request,
                # never authority to hide a second active row for that same
                # incident date.
                raise RuntimeError(
                    "MLB_COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_"
                    "HISTORY_ACTIVE_REQUEST_CONFLICT"
                )
            return _source_pull_rebind_history_response(history)
        # This remediation version is intentionally one incident only. A
        # compact proof for another slate cannot be displaced or expanded.
        raise RuntimeError(
            "MLB_COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_HISTORY_CONFLICT"
        )
    item = _cooperative_record(current_item, slate_date)
    existing_marker = item.get(
        COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_REMEDIATION_FIELD
    )
    if existing_marker is not None:
        _validated_source_pull_rebind_remediation_marker(
            existing_marker,
            item,
        )
        if item.get("state") == COOPERATIVE_REPLAY_REVIEW_REQUIRED:
            # The same request failed again after consuming its one permitted
            # source-rebind retry. Never turn that second defect into a loop.
            raise RuntimeError(
                "MLB_COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_RETRY_CONSUMED"
            )
        return _source_pull_rebind_review_response(
            item,
            idempotent=True,
        )

    progress = _validated_source_pull_rebind_review_checkpoint(item)
    request_epoch = int(item["requested_at_epoch"])
    request_id = str(item.get("request_id") or "")
    checkpoint_fingerprint = str(progress["checkpointFingerprint"])
    now = _utc_now()
    marker = {
        "version": (
            COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_REMEDIATION_VERSION
        ),
        "sourcePullRebindVersion": (
            COOPERATIVE_SOURCE_PULL_REBIND_REQUIRED_VERSION
        ),
        "slateDateEt": slate_date,
        "requestEpoch": request_epoch,
        "requestIdFingerprint": hashlib.sha256(
            request_id.encode("utf-8")
        ).hexdigest(),
        "checkpointFingerprint": checkpoint_fingerprint,
        "errorCode": COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_ERROR,
        "stage": COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_STAGE,
        "appliedAtUtc": now.isoformat(),
        "appliedAtEpoch": int(now.timestamp()),
        "oneShot": True,
    }
    names = {
        "#state": "state",
        "#progress": "terminal_replay_progress",
        "#checkpoint": "checkpointFingerprint",
        "#attempt": "lastAttempt",
        "#error": "errorCode",
        "#stage": "stage",
        "#remediation": (
            COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_REMEDIATION_FIELD
        ),
    }
    values = {
        ":record_type": COOPERATIVE_TERMINAL_REPLAY_RECORD_TYPE,
        ":version": COOPERATIVE_TERMINAL_REPLAY_VERSION,
        ":slate_date": slate_date,
        ":request_epoch": request_epoch,
        ":request_id": request_id,
        ":review_required": COOPERATIVE_REPLAY_REVIEW_REQUIRED,
        ":queued": COOPERATIVE_REPLAY_QUEUED,
        ":progress": progress,
        ":checkpoint": checkpoint_fingerprint,
        ":error": COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_ERROR,
        ":stage": COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_STAGE,
        ":remediation": marker,
    }
    try:
        updated = _cooperative_replay_table().update_item(
            Key=_cooperative_replay_key(),
            ConditionExpression=(
                "record_type = :record_type AND "
                "coordination_version = :version AND "
                "slate_date_et = :slate_date AND "
                "requested_at_epoch = :request_epoch AND "
                "request_id = :request_id AND "
                "#state = :review_required AND "
                "#progress = :progress AND "
                "#progress.#checkpoint = :checkpoint AND "
                "#progress.#attempt.#error = :error AND "
                "#progress.#attempt.#stage = :stage AND "
                "attribute_not_exists(#remediation)"
            ),
            UpdateExpression=(
                "SET #state = :queued, #remediation = :remediation "
                "REMOVE claim_owner, claim_acquired_at_utc, "
                "claim_acquired_at_epoch, claim_expires_at_utc, "
                "claim_expires_at_epoch"
            ),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
            ReturnValues="ALL_NEW",
        ).get("Attributes")
    except BaseException as exc:
        # A lost response is accepted only when the same request is still
        # exactly QUEUED with the exact one-shot marker and unchanged progress.
        observed = _cooperative_record(
            _read_cooperative_replay(),
            slate_date,
        )
        if (
            observed.get("state") == COOPERATIVE_REPLAY_QUEUED
            and observed.get("requested_at_epoch") == request_epoch
            and str(observed.get("request_id") or "") == request_id
            and observed.get("terminal_replay_progress") == progress
            and observed.get(
                COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_REMEDIATION_FIELD
            )
            == marker
        ):
            updated = observed
        else:
            raise RuntimeError(
                "MLB_COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_REQUEUE_FAILED"
            ) from exc

    requeued = _cooperative_record(dict(updated or {}), slate_date)
    if (
        requeued.get("state") != COOPERATIVE_REPLAY_QUEUED
        or requeued.get("requested_at_epoch") != request_epoch
        or str(requeued.get("request_id") or "") != request_id
        or requeued.get("terminal_replay_progress") != progress
        or requeued.get(
            COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_REMEDIATION_FIELD
        )
        != marker
    ):
        raise RuntimeError(
            "MLB_COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_REQUEUE_INVALID"
        )
    return _source_pull_rebind_review_response(
        requeued,
        idempotent=False,
    )


def _prelock_candidate_review_v2_integer(
    value: Any,
    field: str,
) -> int:
    if isinstance(value, bool):
        raise RuntimeError(
            "MLB_COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_"
            f"{field.upper()}_INVALID"
        )
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise RuntimeError(
            "MLB_COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_"
            f"{field.upper()}_INVALID"
        ) from exc
    if (
        not number.is_finite()
        or number < 0
        or number != number.to_integral_value()
    ):
        raise RuntimeError(
            "MLB_COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_"
            f"{field.upper()}_INVALID"
        )
    return int(number)


def _prelock_candidate_review_v2_progress_fingerprint(
    progress: Any,
) -> str:
    """Hash the complete checkpoint, including attempt metadata."""

    return _source_pull_rebind_compact_fingerprint(progress)


def _validated_prelock_candidate_review_v2_stale_checkpoint(
    item: Dict[str, Any],
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    progress = item.get("terminal_replay_progress")
    public = _cooperative_terminal_progress_public(item)
    attempt = (
        progress.get("lastAttempt")
        if isinstance(progress, dict)
        else None
    )
    manifest_authority = (
        progress.get("manifestAuthority")
        if isinstance(progress, dict)
        else None
    )
    roster = (
        manifest_authority.get("gameRoster")
        if isinstance(manifest_authority, Mapping)
        else None
    )
    processed = (
        progress.get("processedGames")
        if isinstance(progress, dict)
        else None
    )
    prior_marker = _validated_source_pull_rebind_remediation_marker(
        item.get(COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_REMEDIATION_FIELD),
        item,
    )
    request_id = str(item.get("request_id") or "")
    request_epoch = _prelock_candidate_review_v2_integer(
        item.get("requested_at_epoch"),
        "request_epoch",
    )
    expected_counters = {
        "manifestGameCount": COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_MANIFEST_COUNT,
        "nextGameIndex": COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_GAME_INDEX,
        "processedGameCount": 1,
        "terminalCount": 1,
        "canonicalCount": 0,
        "noPredictionDataCount": 0,
        "missedLockValidPrelockQuarantineCount": 1,
        "reconciledCount": 1,
        "verificationIndex": 0,
        "verifiedGameCount": 0,
        "attemptCount": COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_ATTEMPT_COUNT,
    }
    counters_match = bool(
        isinstance(public, dict)
        and public.get("valid") is True
        and all(
            public.get(field) == value
            for field, value in expected_counters.items()
        )
    )
    processed_entry = (
        processed[0]
        if isinstance(processed, list)
        and len(processed) == 1
        and isinstance(processed[0], dict)
        else None
    )
    roster_zero = (
        roster[0]
        if isinstance(roster, list)
        and len(roster)
        == COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_MANIFEST_COUNT
        and isinstance(roster[0], Mapping)
        else None
    )
    roster_one = (
        roster[1]
        if isinstance(roster, list)
        and len(roster)
        == COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_MANIFEST_COUNT
        and isinstance(roster[1], Mapping)
        else None
    )
    checkpoint_fingerprint = (
        str(progress.get("checkpointFingerprint") or "")
        if isinstance(progress, dict)
        else ""
    )
    at_utc = (
        str(attempt.get("atUtc") or "")
        if isinstance(attempt, dict)
        else ""
    )
    try:
        parsed_at = datetime.fromisoformat(at_utc)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            "MLB_COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_"
            "CHECKPOINT_INVALID"
        ) from exc
    forbidden_claim_fields = (
        "claim_owner",
        "claim_acquired_at_utc",
        "claim_acquired_at_epoch",
        "claim_expires_at_utc",
        "claim_expires_at_epoch",
    )
    if (
        not request_id
        or request_epoch <= 0
        or item.get("state") != COOPERATIVE_REPLAY_REVIEW_REQUIRED
        or str(item.get("slate_date_et") or "")
        != COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_SLATE
        or _cooperative_review_reason(item)
        != COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_REASON
        or item.get("last_chunk_stage")
        != COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_STALE_STAGE
        or item.get("last_chunk_status")
        != COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_STALE_STATUS
        or any(field in item for field in forbidden_claim_fields)
        or not isinstance(progress, dict)
        or progress.get("version") != COOPERATIVE_TERMINAL_CHUNK_VERSION
        or str(progress.get("slateDateEt") or "")
        != COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_SLATE
        or progress.get("requestEpoch") != request_epoch
        or str(progress.get("requestId") or "") != request_id
        or progress.get("phase") != "PROCESS"
        or progress.get("verificationComplete") is not False
        or progress.get("postStartPredictionCreationAllowed") is not False
        or progress.get("immutablePredictionRewriteAllowed") is not False
        or progress.get("productionAuthorityChanged") is not False
        or not counters_match
        or not isinstance(processed_entry, dict)
        or processed_entry.get("terminalState")
        != "MISSED_LOCK_VALID_PRELOCK_CANDIDATE_NOT_PROMOTED"
        or processed_entry.get("reconciled") is not True
        or not isinstance(roster_zero, Mapping)
        or not isinstance(roster_one, Mapping)
        or processed_entry.get("gameIdentity")
        != roster_zero.get("gameIdentity")
        or processed_entry.get("durableIdentity")
        not in list(roster_zero.get("identityOptions") or [])
        or not isinstance(attempt, dict)
        or set(attempt)
        != {
            "status",
            "stage",
            "atUtc",
            "phase",
            "gameIndex",
            "gameIdentity",
            "durableIdentity",
        }
        or attempt.get("status")
        != COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_STALE_STATUS
        or attempt.get("stage")
        != COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_STALE_STAGE
        or attempt.get("phase") != "PROCESS"
        or attempt.get("gameIndex")
        != COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_PRIOR_GAME_INDEX
        or attempt.get("gameIdentity")
        != processed_entry.get("gameIdentity")
        or attempt.get("durableIdentity")
        != processed_entry.get("durableIdentity")
        or at_utc != str(progress.get("updatedAtUtc") or "")
        or parsed_at.tzinfo is None
        or parsed_at.utcoffset() != timedelta(0)
        or parsed_at.isoformat() != at_utc
        or re.fullmatch(r"[0-9a-f]{64}", checkpoint_fingerprint) is None
        or checkpoint_fingerprint
        != _source_pull_rebind_checkpoint_fingerprint(progress)
        or checkpoint_fingerprint
        == str(prior_marker.get("checkpointFingerprint") or "")
        or re.fullmatch(
            r"[0-9a-f]{64}",
            str(progress.get("manifestFingerprint") or ""),
        )
        is None
        or not isinstance(manifest_authority, Mapping)
        or re.fullmatch(
            r"[0-9a-f]{64}",
            str(
                manifest_authority.get(
                    "authorityEvidenceFingerprint"
                )
                or ""
            ),
        )
        is None
        or re.fullmatch(
            r"[0-9a-f]{64}",
            hashlib.sha256(
                str(roster_one.get("gameIdentity") or "").encode("utf-8")
            ).hexdigest(),
        )
        is None
        or hashlib.sha256(
            str(roster_one.get("gameIdentity") or "").encode("utf-8")
        ).hexdigest()
        != COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_GAME_IDENTITY_FINGERPRINT
    ):
        raise RuntimeError(
            "MLB_COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_"
            "CHECKPOINT_INVALID"
        )
    return copy.deepcopy(progress), copy.deepcopy(prior_marker)


def _validated_prelock_candidate_review_v2_positive_proof(
    value: Any,
    item: Dict[str, Any],
    *,
    review_progress: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    expected_keys = {
        "version",
        "slateDateEt",
        "requestEpoch",
        "requestIdFingerprint",
        "checkpointFingerprint",
        "progressFingerprint",
        "manifestFingerprint",
        "manifestAuthorityEvidenceFingerprint",
        "manifestGameCount",
        "gameIndex",
        "gameIdentityFingerprint",
        "identityBindingMode",
        "boundScoringPullCount",
        "candidateAuthorityVersion",
        "candidateAuthorityFingerprint",
        "candidateProofFingerprint",
        "candidateSnapshotFingerprint",
        "candidateRowFingerprint",
        "candidateSelectionFingerprint",
        "predictionPayloadFingerprint",
        "predictionSourcePullFingerprint",
        "boundScoringFingerprint",
        "terminalAbsent",
        "rejectedNewerCandidateCount",
        "modelOrSignalRecomputedAtLock",
        "predictionAdopted",
        "postStartPredictionCreationAllowed",
        "immutablePredictionRewriteAllowed",
        "productionAuthorityChanged",
        "proofFingerprint",
    }
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise RuntimeError(
            "MLB_COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_PROOF_INVALID"
        )
    request_id = str(item.get("request_id") or "")
    request_epoch = _prelock_candidate_review_v2_integer(
        item.get("requested_at_epoch"),
        "proof_request_epoch",
    )
    fingerprint_fields = (
        "requestIdFingerprint",
        "checkpointFingerprint",
        "progressFingerprint",
        "manifestFingerprint",
        "manifestAuthorityEvidenceFingerprint",
        "gameIdentityFingerprint",
        "candidateAuthorityFingerprint",
        "candidateProofFingerprint",
        "candidateSnapshotFingerprint",
        "candidateRowFingerprint",
        "candidateSelectionFingerprint",
        "predictionPayloadFingerprint",
        "predictionSourcePullFingerprint",
        "boundScoringFingerprint",
        "proofFingerprint",
    )
    proof_material = {
        key: child
        for key, child in value.items()
        if key != "proofFingerprint"
    }
    expected_authority_version = getattr(
        mlb_daily_per_game_lock_patch,
        "VALID_PRELOCK_QUARANTINE_AUTHORITY_VERSION",
        None,
    )
    if (
        not request_id
        or value.get("version")
        != COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_PROOF_VERSION
        or str(value.get("slateDateEt") or "")
        != COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_SLATE
        or _prelock_candidate_review_v2_integer(
            value.get("requestEpoch"),
            "proof_bound_request_epoch",
        )
        != request_epoch
        or value.get("requestIdFingerprint")
        != hashlib.sha256(request_id.encode("utf-8")).hexdigest()
        or _prelock_candidate_review_v2_integer(
            value.get("manifestGameCount"),
            "proof_manifest_count",
        )
        != COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_MANIFEST_COUNT
        or _prelock_candidate_review_v2_integer(
            value.get("gameIndex"),
            "proof_game_index",
        )
        != COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_GAME_INDEX
        or value.get("gameIdentityFingerprint")
        != COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_GAME_IDENTITY_FINGERPRINT
        or value.get("identityBindingMode") != "exact_identity"
        or _prelock_candidate_review_v2_integer(
            value.get("boundScoringPullCount"),
            "proof_bound_pull_count",
        )
        != COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_BOUND_PULL_COUNT
        or not expected_authority_version
        or value.get("candidateAuthorityVersion")
        != expected_authority_version
        or value.get("terminalAbsent") is not True
        or _prelock_candidate_review_v2_integer(
            value.get("rejectedNewerCandidateCount"),
            "proof_rejected_count",
        )
        != 0
        or value.get("modelOrSignalRecomputedAtLock") is not False
        or value.get("predictionAdopted") is not False
        or value.get("postStartPredictionCreationAllowed") is not False
        or value.get("immutablePredictionRewriteAllowed") is not False
        or value.get("productionAuthorityChanged") is not False
        or any(
            re.fullmatch(r"[0-9a-f]{64}", str(value.get(field) or ""))
            is None
            for field in fingerprint_fields
        )
        or value.get("proofFingerprint")
        != _source_pull_rebind_compact_fingerprint(proof_material)
    ):
        raise RuntimeError(
            "MLB_COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_PROOF_INVALID"
        )
    if review_progress is not None:
        manifest_authority = review_progress.get("manifestAuthority")
        if (
            value.get("checkpointFingerprint")
            != review_progress.get("checkpointFingerprint")
            or value.get("progressFingerprint")
            != _prelock_candidate_review_v2_progress_fingerprint(
                review_progress
            )
            or value.get("manifestFingerprint")
            != review_progress.get("manifestFingerprint")
            or not isinstance(manifest_authority, Mapping)
            or value.get("manifestAuthorityEvidenceFingerprint")
            != manifest_authority.get("authorityEvidenceFingerprint")
        ):
            raise RuntimeError(
                "MLB_COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_PROOF_INVALID"
            )
    return copy.deepcopy(value)


def _validated_prelock_candidate_review_v2_marker(
    value: Any,
    item: Dict[str, Any],
) -> Dict[str, Any]:
    expected_keys = {
        "version",
        "proofVersion",
        "reviewCheckpointGuardVersion",
        "priorRemediationVersion",
        "slateDateEt",
        "requestEpoch",
        "requestIdFingerprint",
        "checkpointFingerprint",
        "reviewProgressFingerprint",
        "priorRemediationFingerprint",
        "positiveProof",
        "reviewReason",
        "staleCheckpointStage",
        "staleCheckpointStatus",
        "appliedAtUtc",
        "appliedAtEpoch",
        "oneShot",
    }
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise RuntimeError(
            "MLB_COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_MARKER_INVALID"
        )
    prior = _validated_source_pull_rebind_remediation_marker(
        item.get(COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_REMEDIATION_FIELD),
        item,
    )
    proof = _validated_prelock_candidate_review_v2_positive_proof(
        value.get("positiveProof"),
        item,
    )
    try:
        applied_text = str(value.get("appliedAtUtc") or "")
        applied_at = datetime.fromisoformat(applied_text)
        applied_epoch = _prelock_candidate_review_v2_integer(
            value.get("appliedAtEpoch"),
            "marker_applied_epoch",
        )
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            "MLB_COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_MARKER_INVALID"
        ) from exc
    request_epoch = _prelock_candidate_review_v2_integer(
        item.get("requested_at_epoch"),
        "marker_request_epoch",
    )
    request_id = str(item.get("request_id") or "")
    if (
        value.get("version")
        != COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_REMEDIATION_VERSION
        or value.get("proofVersion")
        != COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_PROOF_VERSION
        or value.get("reviewCheckpointGuardVersion")
        != COOPERATIVE_TERMINAL_REVIEW_CHECKPOINT_GUARD_VERSION
        or value.get("priorRemediationVersion")
        != COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_REMEDIATION_VERSION
        or str(value.get("slateDateEt") or "")
        != COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_SLATE
        or _prelock_candidate_review_v2_integer(
            value.get("requestEpoch"),
            "marker_bound_request_epoch",
        )
        != request_epoch
        or not request_id
        or value.get("requestIdFingerprint")
        != hashlib.sha256(request_id.encode("utf-8")).hexdigest()
        or value.get("checkpointFingerprint")
        != proof.get("checkpointFingerprint")
        or value.get("reviewProgressFingerprint")
        != proof.get("progressFingerprint")
        or value.get("priorRemediationFingerprint")
        != _source_pull_rebind_compact_fingerprint(prior)
        or value.get("reviewReason")
        != COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_REASON
        or value.get("staleCheckpointStage")
        != COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_STALE_STAGE
        or value.get("staleCheckpointStatus")
        != COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_STALE_STATUS
        or applied_at.tzinfo is None
        or applied_at.utcoffset() != timedelta(0)
        or applied_at.isoformat() != applied_text
        or int(applied_at.timestamp()) != applied_epoch
        or value.get("oneShot") is not True
    ):
        raise RuntimeError(
            "MLB_COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_MARKER_INVALID"
        )
    return copy.deepcopy(value)


def _validated_prelock_candidate_review_v2_history(
    value: Any,
) -> Dict[str, Any]:
    expected_keys = {
        "version",
        "remediationVersion",
        "proofVersion",
        "reviewCheckpointGuardVersion",
        "priorRemediationVersion",
        "slateDateEt",
        "requestEpoch",
        "requestIdFingerprint",
        "reviewCheckpointFingerprint",
        "reviewProgressFingerprint",
        "positiveProofFingerprint",
        "completionCheckpointFingerprint",
        "completionReceiptFingerprint",
        "acknowledgedAtUtc",
        "acknowledgedAtEpoch",
        "state",
        "oneShot",
        "proofFingerprint",
    }
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise RuntimeError(
            "MLB_COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_HISTORY_INVALID"
        )
    try:
        request_epoch = _prelock_candidate_review_v2_integer(
            value.get("requestEpoch"),
            "history_request_epoch",
        )
        acknowledged_epoch = _prelock_candidate_review_v2_integer(
            value.get("acknowledgedAtEpoch"),
            "history_acknowledged_epoch",
        )
        acknowledged_text = str(value.get("acknowledgedAtUtc") or "")
        acknowledged_at = datetime.fromisoformat(acknowledged_text)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            "MLB_COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_HISTORY_INVALID"
        ) from exc
    fingerprint_fields = (
        "requestIdFingerprint",
        "reviewCheckpointFingerprint",
        "reviewProgressFingerprint",
        "positiveProofFingerprint",
        "completionCheckpointFingerprint",
        "completionReceiptFingerprint",
        "proofFingerprint",
    )
    material = {
        key: child
        for key, child in value.items()
        if key != "proofFingerprint"
    }
    encoded_size = len(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    )
    if (
        request_epoch <= 0
        or acknowledged_epoch <= 0
        or value.get("version")
        != COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_HISTORY_VERSION
        or value.get("remediationVersion")
        != COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_REMEDIATION_VERSION
        or value.get("proofVersion")
        != COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_PROOF_VERSION
        or value.get("reviewCheckpointGuardVersion")
        != COOPERATIVE_TERMINAL_REVIEW_CHECKPOINT_GUARD_VERSION
        or value.get("priorRemediationVersion")
        != COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_REMEDIATION_VERSION
        or value.get("slateDateEt")
        != COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_SLATE
        or acknowledged_at.tzinfo is None
        or acknowledged_at.utcoffset() != timedelta(0)
        or acknowledged_at.isoformat() != acknowledged_text
        or int(acknowledged_at.timestamp()) != acknowledged_epoch
        or value.get("state") != COOPERATIVE_REPLAY_ACKNOWLEDGED
        or value.get("oneShot") is not True
        or any(
            re.fullmatch(r"[0-9a-f]{64}", str(value.get(field) or ""))
            is None
            for field in fingerprint_fields
        )
        or value.get("proofFingerprint")
        != _source_pull_rebind_compact_fingerprint(material)
        or encoded_size
        > COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_HISTORY_MAX_BYTES
    ):
        raise RuntimeError(
            "MLB_COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_HISTORY_INVALID"
        )
    return copy.deepcopy(value)


def _prelock_candidate_review_v2_completed_history(
    item: Dict[str, Any],
) -> Dict[str, Any]:
    if item.get("state") != COOPERATIVE_REPLAY_ACKNOWLEDGED:
        raise RuntimeError(
            "MLB_COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_"
            "HISTORY_NOT_ACKNOWLEDGED"
        )
    marker = _validated_prelock_candidate_review_v2_marker(
        item.get(COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_FIELD),
        item,
    )
    receipt = _validated_persisted_replay_receipt(item)
    try:
        acknowledged_text = str(item.get("acknowledged_at_utc") or "")
        acknowledged_at = datetime.fromisoformat(acknowledged_text)
        acknowledged_epoch = _prelock_candidate_review_v2_integer(
            item.get("acknowledged_at_epoch"),
            "completed_history_acknowledged_epoch",
        )
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            "MLB_COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_HISTORY_INVALID"
        ) from exc
    if (
        acknowledged_at.tzinfo is None
        or acknowledged_at.utcoffset() != timedelta(0)
        or acknowledged_at.isoformat() != acknowledged_text
        or int(acknowledged_at.timestamp()) != acknowledged_epoch
        or acknowledged_epoch
        < _prelock_candidate_review_v2_integer(
            marker.get("appliedAtEpoch"),
            "completed_history_applied_epoch",
        )
    ):
        raise RuntimeError(
            "MLB_COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_HISTORY_INVALID"
        )
    proof = marker["positiveProof"]
    history = {
        "version": COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_HISTORY_VERSION,
        "remediationVersion": (
            COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_REMEDIATION_VERSION
        ),
        "proofVersion": COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_PROOF_VERSION,
        "reviewCheckpointGuardVersion": (
            COOPERATIVE_TERMINAL_REVIEW_CHECKPOINT_GUARD_VERSION
        ),
        "priorRemediationVersion": (
            COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_REMEDIATION_VERSION
        ),
        "slateDateEt": COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_SLATE,
        "requestEpoch": _prelock_candidate_review_v2_integer(
            item.get("requested_at_epoch"),
            "completed_history_request_epoch",
        ),
        "requestIdFingerprint": marker["requestIdFingerprint"],
        "reviewCheckpointFingerprint": marker["checkpointFingerprint"],
        "reviewProgressFingerprint": marker["reviewProgressFingerprint"],
        "positiveProofFingerprint": proof["proofFingerprint"],
        "completionCheckpointFingerprint": str(
            receipt.get("checkpointFingerprint") or ""
        ),
        "completionReceiptFingerprint": (
            _source_pull_rebind_compact_fingerprint(receipt)
        ),
        "acknowledgedAtUtc": acknowledged_text,
        "acknowledgedAtEpoch": acknowledged_epoch,
        "state": COOPERATIVE_REPLAY_ACKNOWLEDGED,
        "oneShot": True,
    }
    history["proofFingerprint"] = (
        _source_pull_rebind_compact_fingerprint(history)
    )
    return _validated_prelock_candidate_review_v2_history(history)


def _prelock_candidate_review_v2_history_for_replacement(
    item: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    _validated_persisted_replay_receipt(item)
    raw_history = item.get(
        COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_HISTORY_FIELD
    )
    history = (
        _validated_prelock_candidate_review_v2_history(raw_history)
        if raw_history is not None
        else None
    )
    marker = item.get(COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_FIELD)
    if marker is not None:
        completed = _prelock_candidate_review_v2_completed_history(item)
        if history is not None and history != completed:
            raise RuntimeError(
                "MLB_COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_"
                "HISTORY_CONFLICT"
            )
        history = completed
    return copy.deepcopy(history) if history is not None else None


def _prelock_candidate_review_v2_response(
    item: Dict[str, Any],
    *,
    idempotent: bool,
) -> Dict[str, Any]:
    state = str(item.get("state") or "")
    status_by_state = {
        COOPERATIVE_REPLAY_QUEUED: "QUEUED_FOR_EVENTBRIDGE_LOCK_OWNER",
        COOPERATIVE_REPLAY_CLAIMED: "CLAIMED_BY_EVENTBRIDGE_LOCK_OWNER",
        COOPERATIVE_REPLAY_COMPLETED: "COMPLETED_BY_EVENTBRIDGE_LOCK_OWNER",
        COOPERATIVE_REPLAY_ACKNOWLEDGED: "ACKNOWLEDGED_COMPLETION",
    }
    status = status_by_state.get(state)
    if status is None:
        raise RuntimeError(
            "MLB_COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_STATE_INVALID"
        )
    if state in {
        COOPERATIVE_REPLAY_COMPLETED,
        COOPERATIVE_REPLAY_ACKNOWLEDGED,
    }:
        _validated_persisted_replay_receipt(item)
    return {
        "ok": True,
        "sport": "mlb",
        "slateDateEt": str(item.get("slate_date_et") or ""),
        "status": status,
        "reason": status,
        "skipped": True,
        "mutatingRunAttempted": False,
        "cooperativeTerminalReplayCompleted": state
        in {COOPERATIVE_REPLAY_COMPLETED, COOPERATIVE_REPLAY_ACKNOWLEDGED},
        "cooperativeTerminalReplay": _cooperative_public_state(item),
        "prelockCandidateReviewV2RemediationApplied": True,
        "prelockCandidateReviewV2RemediationIdempotent": bool(idempotent),
        "prelockCandidateReviewV2RemediationVersion": (
            COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_REMEDIATION_VERSION
        ),
        "installedRuntimePositiveProofBound": True,
        "priorSourcePullRebindRemediationValidated": True,
        "automaticRetryAllowed": False,
        "activeLeaseMutationAllowed": False,
        "postStartPredictionCreationAllowed": False,
        "immutablePredictionRewriteAllowed": False,
        "directWorkflowTableWrite": False,
        "productionAuthorityChanged": False,
    }


def _prelock_candidate_review_v2_history_response(
    history: Dict[str, Any],
) -> Dict[str, Any]:
    history = _validated_prelock_candidate_review_v2_history(history)
    slate_date = str(history["slateDateEt"])
    public = {
        "version": COOPERATIVE_TERMINAL_REPLAY_VERSION,
        "state": COOPERATIVE_REPLAY_ACKNOWLEDGED,
        "slateDateEt": slate_date,
        "automaticExecutionOwner": "eventbridge_daily_lock_schedule",
        "currentSlateRunsFirst": True,
        "freshPriorOwnerProofMayCarryAcrossInvocation": True,
        "currentSlateSuccessProofPresent": False,
        "activeLeaseMutationAllowed": False,
        "postStartPredictionCreationAllowed": False,
        "immutablePredictionRewriteAllowed": False,
        "directWorkflowTableWrite": False,
        "productionAuthorityChanged": False,
        "ownerIdentifierExposed": False,
        "terminalChunkProgress": None,
        "durableRemediationHistory": True,
        "failClosed": True,
    }
    return {
        "ok": True,
        "sport": "mlb",
        "slateDateEt": slate_date,
        "status": "ACKNOWLEDGED_COMPLETION",
        "reason": "ACKNOWLEDGED_COMPLETION",
        "skipped": True,
        "mutatingRunAttempted": False,
        "cooperativeTerminalReplayCompleted": True,
        "cooperativeTerminalReplay": public,
        "prelockCandidateReviewV2RemediationApplied": True,
        "prelockCandidateReviewV2RemediationIdempotent": True,
        "prelockCandidateReviewV2RemediationVersion": (
            COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_REMEDIATION_VERSION
        ),
        "installedRuntimePositiveProofBound": True,
        "priorSourcePullRebindRemediationValidated": True,
        "prelockCandidateReviewV2DurableHistory": True,
        "automaticRetryAllowed": False,
        "activeLeaseMutationAllowed": False,
        "postStartPredictionCreationAllowed": False,
        "immutablePredictionRewriteAllowed": False,
        "directWorkflowTableWrite": False,
        "productionAuthorityChanged": False,
    }


def _requeue_prelock_candidate_review_after_installed_runtime_proof_v2(
    event: Dict[str, Any],
) -> Dict[str, Any]:
    slate_date = _strict_historical_slate_date(event)
    if (
        event.get(COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_EVENT_FLAG)
        is not True
        or set(event)
        != COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_EVENT_KEYS
        or event.get("acknowledgeCooperativeCompletion") is True
        or COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_EVENT_FLAG in event
    ):
        raise RuntimeError(
            "MLB_COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_FLAG_MISSING"
        )
    if slate_date != COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_SLATE:
        raise RuntimeError(
            "MLB_COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_SLATE_INVALID"
        )
    prover = getattr(
        mlb_daily_pick_lock,
        "prove_cooperative_prelock_candidate_review_v2",
        None,
    )
    if (
        not callable(prover)
        or getattr(
            mlb_daily_pick_lock,
            "MLB_COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_PROOF_VERSION",
            None,
        )
        != COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_PROOF_VERSION
    ):
        raise RuntimeError(
            "MLB_COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_"
            "INSTALLED_RUNTIME_NOT_READY"
        )

    raw = _read_cooperative_replay()
    if not raw or not str(raw.get("request_id") or "").strip():
        raise RuntimeError(
            "MLB_COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_RECORD_INVALID"
        )
    current_date = str(raw.get("slate_date_et") or "")
    current_item = _cooperative_record(raw, current_date)
    raw_history = current_item.get(
        COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_HISTORY_FIELD
    )
    if raw_history is not None:
        history = _validated_prelock_candidate_review_v2_history(raw_history)
        if history.get("slateDateEt") == slate_date:
            if current_date == slate_date:
                raise RuntimeError(
                    "MLB_COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_"
                    "HISTORY_ACTIVE_REQUEST_CONFLICT"
                )
            return _prelock_candidate_review_v2_history_response(history)
        raise RuntimeError(
            "MLB_COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_HISTORY_CONFLICT"
        )

    item = _cooperative_record(current_item, slate_date)
    existing_marker = item.get(
        COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_FIELD
    )
    if existing_marker is not None:
        _validated_prelock_candidate_review_v2_marker(
            existing_marker,
            item,
        )
        if item.get("state") == COOPERATIVE_REPLAY_REVIEW_REQUIRED:
            raise RuntimeError(
                "MLB_COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_RETRY_CONSUMED"
            )
        return _prelock_candidate_review_v2_response(
            item,
            idempotent=True,
        )

    progress, prior_marker = (
        _validated_prelock_candidate_review_v2_stale_checkpoint(item)
    )
    request_epoch = _prelock_candidate_review_v2_integer(
        item.get("requested_at_epoch"),
        "requeue_request_epoch",
    )
    request_id = str(item.get("request_id") or "")
    positive_proof = prover(
        slate_date=slate_date,
        request_epoch=request_epoch,
        request_id=request_id,
        checkpoint=copy.deepcopy(progress),
    )
    positive_proof = (
        _validated_prelock_candidate_review_v2_positive_proof(
            positive_proof,
            item,
            review_progress=progress,
        )
    )
    now = _utc_now()
    marker = {
        "version": (
            COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_REMEDIATION_VERSION
        ),
        "proofVersion": (
            COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_PROOF_VERSION
        ),
        "reviewCheckpointGuardVersion": (
            COOPERATIVE_TERMINAL_REVIEW_CHECKPOINT_GUARD_VERSION
        ),
        "priorRemediationVersion": (
            COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_REMEDIATION_VERSION
        ),
        "slateDateEt": slate_date,
        "requestEpoch": request_epoch,
        "requestIdFingerprint": hashlib.sha256(
            request_id.encode("utf-8")
        ).hexdigest(),
        "checkpointFingerprint": str(
            progress.get("checkpointFingerprint") or ""
        ),
        "reviewProgressFingerprint": (
            _prelock_candidate_review_v2_progress_fingerprint(progress)
        ),
        "priorRemediationFingerprint": (
            _source_pull_rebind_compact_fingerprint(prior_marker)
        ),
        "positiveProof": copy.deepcopy(positive_proof),
        "reviewReason": COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_REASON,
        "staleCheckpointStage": (
            COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_STALE_STAGE
        ),
        "staleCheckpointStatus": (
            COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_STALE_STATUS
        ),
        "appliedAtUtc": now.isoformat(),
        "appliedAtEpoch": int(now.timestamp()),
        "oneShot": True,
    }
    _validated_prelock_candidate_review_v2_marker(marker, item)
    names = {
        "#state": "state",
        "#progress": "terminal_replay_progress",
        "#checkpoint": "checkpointFingerprint",
        "#attempt": "lastAttempt",
        "#error": "errorCode",
        "#attempt_stage": "stage",
        "#attempt_status": "status",
        "#last_stage": "last_chunk_stage",
        "#last_status": "last_chunk_status",
        "#prior": COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_REMEDIATION_FIELD,
        "#remediation": COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_FIELD,
    }
    values = {
        ":record_type": COOPERATIVE_TERMINAL_REPLAY_RECORD_TYPE,
        ":version": COOPERATIVE_TERMINAL_REPLAY_VERSION,
        ":slate_date": slate_date,
        ":request_epoch": request_epoch,
        ":request_id": request_id,
        ":review_required": COOPERATIVE_REPLAY_REVIEW_REQUIRED,
        ":queued": COOPERATIVE_REPLAY_QUEUED,
        ":progress": progress,
        ":checkpoint": progress["checkpointFingerprint"],
        ":stale_stage": COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_STALE_STAGE,
        ":stale_status": COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_STALE_STATUS,
        ":prior": prior_marker,
        ":remediation": marker,
    }
    try:
        updated = _cooperative_replay_table().update_item(
            Key=_cooperative_replay_key(),
            ConditionExpression=(
                "record_type = :record_type AND "
                "coordination_version = :version AND "
                "slate_date_et = :slate_date AND "
                "requested_at_epoch = :request_epoch AND "
                "request_id = :request_id AND "
                "#state = :review_required AND "
                "#progress = :progress AND "
                "#progress.#checkpoint = :checkpoint AND "
                "#progress.#attempt.#attempt_stage = :stale_stage AND "
                "#progress.#attempt.#attempt_status = :stale_status AND "
                "attribute_not_exists(#progress.#attempt.#error) AND "
                "#last_stage = :stale_stage AND "
                "#last_status = :stale_status AND "
                "#prior = :prior AND "
                "attribute_not_exists(#remediation) AND "
                "attribute_not_exists(claim_owner) AND "
                "attribute_not_exists(claim_acquired_at_epoch) AND "
                "attribute_not_exists(claim_expires_at_epoch)"
            ),
            UpdateExpression=(
                "SET #state = :queued, #remediation = :remediation "
                "REMOVE claim_owner, claim_acquired_at_utc, "
                "claim_acquired_at_epoch, claim_expires_at_utc, "
                "claim_expires_at_epoch"
            ),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
            ReturnValues="ALL_NEW",
        ).get("Attributes")
    except BaseException as exc:
        observed = _cooperative_record(
            _read_cooperative_replay(),
            slate_date,
        )
        if (
            observed.get("requested_at_epoch") == request_epoch
            and str(observed.get("request_id") or "") == request_id
            and observed.get(
                COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_REMEDIATION_FIELD
            )
            == prior_marker
            and observed.get(
                COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_FIELD
            )
            == marker
        ):
            _validated_prelock_candidate_review_v2_marker(marker, observed)
            if observed.get("state") == COOPERATIVE_REPLAY_REVIEW_REQUIRED:
                raise RuntimeError(
                    "MLB_COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_"
                    "RETRY_CONSUMED"
                ) from exc
            return _prelock_candidate_review_v2_response(
                observed,
                idempotent=True,
            )
        raise RuntimeError(
            "MLB_COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_REQUEUE_FAILED"
        ) from exc

    requeued = _cooperative_record(dict(updated or {}), slate_date)
    if (
        requeued.get("state") != COOPERATIVE_REPLAY_QUEUED
        or requeued.get("requested_at_epoch") != request_epoch
        or str(requeued.get("request_id") or "") != request_id
        or requeued.get("terminal_replay_progress") != progress
        or requeued.get(
            COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_REMEDIATION_FIELD
        )
        != prior_marker
        or requeued.get(
            COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_FIELD
        )
        != marker
    ):
        raise RuntimeError(
            "MLB_COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_REQUEUE_INVALID"
        )
    return _prelock_candidate_review_v2_response(
        requeued,
        idempotent=False,
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
    replace_acknowledged_item: Optional[Dict[str, Any]] = None
    carried_remediation_history: Optional[Dict[str, Any]] = None
    carried_prelock_v2_history: Optional[Dict[str, Any]] = None
    if existing:
        existing_date = str(existing.get("slate_date_et") or "")
        validated = _cooperative_record(existing, existing_date)
        raw_prelock_v2_history = validated.get(
            COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_HISTORY_FIELD
        )
        if raw_prelock_v2_history is not None:
            prelock_v2_history = (
                _validated_prelock_candidate_review_v2_history(
                    raw_prelock_v2_history
                )
            )
            if prelock_v2_history.get("slateDateEt") == slate_date:
                return _prelock_candidate_review_v2_history_response(
                    prelock_v2_history
                )
        raw_history = validated.get(
            COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_HISTORY_FIELD
        )
        if raw_history is not None:
            remediation_history = (
                _validated_source_pull_rebind_remediation_history(
                    raw_history
                )
            )
            if remediation_history.get("slateDateEt") == slate_date:
                # The one incident-specific retry has already completed and
                # been compacted. A normal replay request cannot route around
                # that durable one-shot fence by replacing the singleton row.
                # Return the same compact completion projection so the normal
                # v5 client can bind it to a fresh official read without any
                # queue, lease, or historical mutation.
                return _source_pull_rebind_history_response(
                    remediation_history
                )
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
        replace_acknowledged_item = validated
        carried_remediation_history = (
            _source_pull_rebind_history_for_replacement(validated)
        )
        carried_prelock_v2_history = (
            _prelock_candidate_review_v2_history_for_replacement(
                validated
            )
        )
        if carried_remediation_history is not None:
            item[COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_HISTORY_FIELD] = (
                copy.deepcopy(carried_remediation_history)
            )
        if carried_prelock_v2_history is not None:
            item[
                COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_HISTORY_FIELD
            ] = copy.deepcopy(carried_prelock_v2_history)

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
            if replace_acknowledged_item is None:
                raise RuntimeError(
                    "MLB_COOPERATIVE_REPLAY_REPLACEMENT_PROOF_MISSING"
                )
            previous_history = replace_acknowledged_item.get(
                COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_HISTORY_FIELD
            )
            previous_marker = replace_acknowledged_item.get(
                COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_REMEDIATION_FIELD
            )
            previous_v2_history = replace_acknowledged_item.get(
                COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_HISTORY_FIELD
            )
            previous_v2_marker = replace_acknowledged_item.get(
                COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_FIELD
            )
            replacement_condition = (
                "record_type = :record_type AND "
                "coordination_version = :version AND "
                "slate_date_et = :previous_slate_date AND "
                "requested_at_epoch = :previous_request_epoch AND "
                "request_id = :previous_request_id AND "
                "#state = :acknowledged AND "
                "terminal_replay_progress = :previous_progress AND "
                "replay_receipt = :previous_receipt AND "
                "acknowledged_at_utc = :previous_acknowledged_at_utc AND "
                "acknowledged_at_epoch = :previous_acknowledged_at_epoch AND "
                + (
                    "attribute_not_exists(#history) AND "
                    if previous_history is None
                    else "#history = :previous_history AND "
                )
                + (
                    "attribute_not_exists(#remediation) AND "
                    if previous_marker is None
                    else "#remediation = :previous_remediation AND "
                )
                + (
                    "attribute_not_exists(#v2_history) AND "
                    if previous_v2_history is None
                    else "#v2_history = :previous_v2_history AND "
                )
                + (
                    "attribute_not_exists(#v2_remediation)"
                    if previous_v2_marker is None
                    else "#v2_remediation = :previous_v2_remediation"
                )
            )
            replacement_values = {
                ":record_type": COOPERATIVE_TERMINAL_REPLAY_RECORD_TYPE,
                ":version": COOPERATIVE_TERMINAL_REPLAY_VERSION,
                ":previous_slate_date": replace_acknowledged_date,
                ":previous_request_epoch": replace_acknowledged_item.get(
                    "requested_at_epoch"
                ),
                ":previous_request_id": replace_acknowledged_item.get(
                    "request_id"
                ),
                ":acknowledged": COOPERATIVE_REPLAY_ACKNOWLEDGED,
                ":previous_progress": replace_acknowledged_item.get(
                    "terminal_replay_progress"
                ),
                ":previous_receipt": replace_acknowledged_item.get(
                    "replay_receipt"
                ),
                ":previous_acknowledged_at_utc": (
                    replace_acknowledged_item.get("acknowledged_at_utc")
                ),
                ":previous_acknowledged_at_epoch": (
                    replace_acknowledged_item.get("acknowledged_at_epoch")
                ),
            }
            if previous_history is not None:
                replacement_values[":previous_history"] = previous_history
            if previous_marker is not None:
                replacement_values[":previous_remediation"] = (
                    previous_marker
                )
            if previous_v2_history is not None:
                replacement_values[":previous_v2_history"] = (
                    previous_v2_history
                )
            if previous_v2_marker is not None:
                replacement_values[":previous_v2_remediation"] = (
                    previous_v2_marker
                )
            table.put_item(
                Item=item,
                ConditionExpression=replacement_condition,
                ExpressionAttributeNames={
                    "#state": "state",
                    "#history": (
                        COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_HISTORY_FIELD
                    ),
                    "#remediation": (
                        COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_REMEDIATION_FIELD
                    ),
                    "#v2_history": (
                        COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_HISTORY_FIELD
                    ),
                    "#v2_remediation": (
                        COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_FIELD
                    ),
                },
                ExpressionAttributeValues=replacement_values,
            )
        return _cooperative_request_response(item)
    except BaseException as exc:
        # Both a normal race and an ambiguous write are resolved by one strong
        # read.  Only the exact same request is accepted idempotently.
        observed = _read_cooperative_replay()
        if observed:
            try:
                validated = _cooperative_record(observed, slate_date)
                observed_history = validated.get(
                    COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_HISTORY_FIELD
                )
                observed_v2_history = validated.get(
                    COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_HISTORY_FIELD
                )
                if (
                    validated.get("requested_at_epoch")
                    != item.get("requested_at_epoch")
                    or str(validated.get("request_id") or "")
                    != str(item.get("request_id") or "")
                    or validated.get(
                        COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_REMEDIATION_FIELD
                    )
                    is not None
                    or validated.get(
                        COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_FIELD
                    )
                    is not None
                    or observed_history != carried_remediation_history
                    or observed_v2_history != carried_prelock_v2_history
                ):
                    raise RuntimeError(
                        "MLB_COOPERATIVE_REPLAY_QUEUE_CONFLICT"
                    )
                if observed_history is not None:
                    _validated_source_pull_rebind_remediation_history(
                        observed_history
                    )
                if observed_v2_history is not None:
                    _validated_prelock_candidate_review_v2_history(
                        observed_v2_history
                    )
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
    state = item.get("state")
    if state in {
        COOPERATIVE_REPLAY_COMPLETED,
        COOPERATIVE_REPLAY_ACKNOWLEDGED,
    }:
        receipt = _validated_persisted_replay_receipt(item)
    else:
        receipt = None
    if state == COOPERATIVE_REPLAY_ACKNOWLEDGED:
        return {
            "ok": True,
            "sport": "mlb",
            "slateDateEt": slate_date,
            "cooperativeTerminalReplayAcknowledged": True,
            "cooperativeTerminalReplay": _cooperative_public_state(item),
        }
    if state != COOPERATIVE_REPLAY_COMPLETED or receipt is None:
        raise RuntimeError("MLB_COOPERATIVE_REPLAY_NOT_COMPLETE")
    request_epoch = int(item["requested_at_epoch"])
    request_id = str(item.get("request_id") or "")
    expected_progress = copy.deepcopy(item.get("terminal_replay_progress"))
    expected_receipt = copy.deepcopy(receipt)
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
                "#progress = :expected_progress AND "
                "#receipt = :expected_receipt AND "
                "#state = :completed"
            ),
            UpdateExpression=(
                "SET #state = :acknowledged, acknowledged_at_utc = :now_utc, "
                "acknowledged_at_epoch = :now_epoch"
            ),
            ExpressionAttributeNames={
                "#state": "state",
                "#progress": "terminal_replay_progress",
                "#receipt": "replay_receipt",
            },
            ExpressionAttributeValues={
                ":record_type": COOPERATIVE_TERMINAL_REPLAY_RECORD_TYPE,
                ":version": COOPERATIVE_TERMINAL_REPLAY_VERSION,
                ":slate_date": slate_date,
                ":request_epoch": request_epoch,
                ":request_id": request_id,
                ":expected_progress": expected_progress,
                ":expected_receipt": expected_receipt,
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
            if (
                observed.get("state")
                != COOPERATIVE_REPLAY_ACKNOWLEDGED
                or observed.get("requested_at_epoch") != request_epoch
                or str(observed.get("request_id") or "") != request_id
                or observed.get("terminal_replay_progress")
                != expected_progress
                or observed.get("replay_receipt") != expected_receipt
            ):
                raise RuntimeError(
                    "MLB_COOPERATIVE_REPLAY_ACKNOWLEDGE_FAILED"
                ) from exc
            _validated_persisted_replay_receipt(observed)
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
    acknowledged = _cooperative_record(dict(updated or {}), slate_date)
    if (
        acknowledged.get("state") != COOPERATIVE_REPLAY_ACKNOWLEDGED
        or acknowledged.get("requested_at_epoch") != request_epoch
        or str(acknowledged.get("request_id") or "") != request_id
        or acknowledged.get("terminal_replay_progress")
        != expected_progress
        or acknowledged.get("replay_receipt") != expected_receipt
    ):
        raise RuntimeError("MLB_COOPERATIVE_REPLAY_ACKNOWLEDGE_FAILED")
    _validated_persisted_replay_receipt(acknowledged)
    return {
        "ok": True,
        "sport": "mlb",
        "slateDateEt": slate_date,
        "cooperativeTerminalReplayAcknowledged": True,
        "cooperativeTerminalReplay": _cooperative_public_state(
            acknowledged
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
    gateway_transport = bool(
        "statusCode" in response or "body" in response
    )
    # The in-process chunk and persisted COMPLETED/ACK records carry one
    # already-decoded, schema-validated receipt. Lambda/API Gateway carries an
    # envelope instead. Never merge the two transports: conflicting top-level
    # receipt material beside an envelope is ambiguous and fails closed.
    if not gateway_transport:
        return copy.deepcopy(response)
    if "body" not in response or any(
        field in response
        for field in {
            "ok",
            "sport",
            "slateDateEt",
            "terminalChunkVersion",
            "perGameLockProgress",
            "missedLockTerminalReconciliation",
        }
    ):
        raise RuntimeError("MLB_COOPERATIVE_REPLAY_RESPONSE_INVALID")
    status_code = _nonnegative_receipt_integer(
        response.get("statusCode", 200),
        "response_status_code",
    )
    body = response.get("body")
    try:
        payload = (
            json.loads(body)
            if isinstance(body, str)
            else copy.deepcopy(body)
        )
    except Exception as exc:
        raise RuntimeError("MLB_COOPERATIVE_REPLAY_BODY_INVALID") from exc
    if (
        status_code < 200
        or status_code >= 300
        or not isinstance(payload, dict)
    ):
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



def _validated_cooperative_review_checkpoint_transition(
    item: Dict[str, Any],
    progress: Any,
    *,
    expected_stage: Any,
    expected_error_code: Any,
) -> Dict[str, Any]:
    """Require a fresh producer failure checkpoint before REVIEW_REQUIRED."""

    prior_present = "terminal_replay_progress" in item
    prior = item.get("terminal_replay_progress")
    prior_absent = not prior_present
    slate_date = str(item.get("slate_date_et") or "")
    request_id = str(item.get("request_id") or "")
    try:
        request_epoch = _nonnegative_receipt_integer(
            item.get("requested_at_epoch"),
            "review_request_epoch",
        )
        prior_attempt_count = (
            0
            if prior_absent
            else _nonnegative_receipt_integer(
                prior.get("attemptCount")
                if isinstance(prior, dict)
                else None,
                "prior_review_attempt_count",
            )
        )
        attempt_count = _nonnegative_receipt_integer(
            progress.get("attemptCount")
            if isinstance(progress, dict)
            else None,
            "review_attempt_count",
        )
    except RuntimeError as exc:
        raise RuntimeError(
            "MLB_COOPERATIVE_TERMINAL_REVIEW_CHECKPOINT_CONTRACT_INVALID"
        ) from exc
    stage = str(expected_stage or "")
    error_code = str(expected_error_code or "")
    attempt = (
        progress.get("lastAttempt")
        if isinstance(progress, dict)
        else None
    )
    manifest_authority = (
        progress.get("manifestAuthority")
        if isinstance(progress, dict)
        else None
    )
    roster = (
        manifest_authority.get("gameRoster")
        if isinstance(manifest_authority, Mapping)
        else None
    )
    phase = str(progress.get("phase") or "") if isinstance(progress, dict) else ""
    counter_fields = (
        "processedGameCount",
        "terminalCount",
        "canonicalCount",
        "noPredictionDataCount",
        "missedLockValidPrelockQuarantineCount",
        "reconciledCount",
        "verificationIndex",
        "verifiedGameCount",
    )
    try:
        cursor = _nonnegative_receipt_integer(
            (
                progress.get("nextGameIndex")
                if phase == "PROCESS"
                else progress.get("verificationIndex")
            )
            if isinstance(progress, dict)
            else None,
            "review_cursor",
        )
        manifest_count = _nonnegative_receipt_integer(
            progress.get("manifestGameCount")
            if isinstance(progress, dict)
            else None,
            "review_manifest_count",
        )
        attempt_game_index = _nonnegative_receipt_integer(
            attempt.get("gameIndex")
            if isinstance(attempt, dict)
            else None,
            "review_attempt_game_index",
        )
        counters = {
            field: _nonnegative_receipt_integer(
                progress.get(field)
                if isinstance(progress, dict)
                else None,
                f"review_{field}",
            )
            for field in counter_fields
        }
    except RuntimeError as exc:
        raise RuntimeError(
            "MLB_COOPERATIVE_TERMINAL_REVIEW_CHECKPOINT_CONTRACT_INVALID"
        ) from exc

    expected_identity = ""
    identity_options: Any = None
    if (
        isinstance(roster, list)
        and 0 <= cursor < len(roster)
        and isinstance(roster[cursor], Mapping)
    ):
        expected_identity = str(
            roster[cursor].get("gameIdentity") or ""
        )
        identity_options = roster[cursor].get("identityOptions")

    at_utc = str(attempt.get("atUtc") or "") if isinstance(attempt, dict) else ""
    claim_at_utc = str(item.get("claim_acquired_at_utc") or "")
    prior_updated_at_utc = (
        str(prior.get("updatedAtUtc") or "")
        if isinstance(prior, dict)
        else ""
    )
    try:
        parsed_at = datetime.fromisoformat(at_utc)
        parsed_claim_at = datetime.fromisoformat(claim_at_utc)
        parsed_prior_updated_at = (
            datetime.fromisoformat(prior_updated_at_utc)
            if isinstance(prior, dict)
            else None
        )
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            "MLB_COOPERATIVE_TERMINAL_REVIEW_CHECKPOINT_CONTRACT_INVALID"
        ) from exc

    prior_fingerprint = (
        str(prior.get("checkpointFingerprint") or "")
        if isinstance(prior, dict)
        else ""
    )
    progress_fingerprint = (
        str(progress.get("checkpointFingerprint") or "")
        if isinstance(progress, dict)
        else ""
    )
    initial_review_shape = bool(
        prior_absent
        and phase == "PROCESS"
        and cursor == 0
        and attempt_count == 1
        and all(counters[field] == 0 for field in counter_fields)
        and progress.get("processedGames") == []
        and progress.get("verificationComplete") is False
    )
    continuing_review_shape = bool(
        isinstance(prior, dict)
        and prior.get("version") == COOPERATIVE_TERMINAL_CHUNK_VERSION
        and str(prior.get("slateDateEt") or "") == slate_date
        and prior.get("requestEpoch") == request_epoch
        and str(prior.get("requestId") or "") == request_id
        and phase == str(prior.get("phase") or "")
        and progress_fingerprint == prior_fingerprint
        and prior_fingerprint
        == _source_pull_rebind_checkpoint_fingerprint(prior)
        and parsed_prior_updated_at is not None
        and parsed_prior_updated_at.tzinfo is not None
        and parsed_prior_updated_at.utcoffset() == timedelta(0)
        and parsed_prior_updated_at.isoformat() == prior_updated_at_utc
        and parsed_at > parsed_prior_updated_at
    )
    valid_cursor_identity = bool(
        (
            cursor < manifest_count
            and isinstance(roster, list)
            and len(roster) == manifest_count
            and expected_identity
            and isinstance(identity_options, list)
            and identity_options
            and str(identity_options[0] or "") == expected_identity
            and isinstance(attempt, dict)
            and str(attempt.get("gameIdentity") or "")
            == expected_identity
            and (
                "durableIdentity" not in attempt
                or str(attempt.get("durableIdentity") or "")
                in {str(value) for value in identity_options}
            )
        )
        or (
            phase == "VERIFY"
            and cursor == manifest_count
            and isinstance(attempt, dict)
            and "gameIdentity" not in attempt
            and "durableIdentity" not in attempt
        )
    )
    if (
        not request_id
        or request_epoch <= 0
        or (prior_present and not isinstance(prior, dict))
        or not isinstance(progress, dict)
        or progress.get("version") != COOPERATIVE_TERMINAL_CHUNK_VERSION
        or str(progress.get("slateDateEt") or "") != slate_date
        or progress.get("requestEpoch") != request_epoch
        or str(progress.get("requestId") or "") != request_id
        or phase not in {"PROCESS", "VERIFY"}
        or not _cooperative_replay_requires_review(stage, error_code)
        or not (initial_review_shape or continuing_review_shape)
        or manifest_count < 1
        or cursor > manifest_count
        or not valid_cursor_identity
        or attempt_count != prior_attempt_count + 1
        or not isinstance(attempt, dict)
        or attempt.get("status") != "FAILED_CLOSED"
        or str(attempt.get("stage") or "") != stage
        or str(attempt.get("errorCode") or "") != error_code
        or str(attempt.get("phase") or "") != phase
        or attempt_game_index != cursor
        or at_utc != str(progress.get("updatedAtUtc") or "")
        or parsed_at.tzinfo is None
        or parsed_at.utcoffset() != timedelta(0)
        or parsed_at.isoformat() != at_utc
        or parsed_claim_at.tzinfo is None
        or parsed_claim_at.utcoffset() != timedelta(0)
        or parsed_claim_at.isoformat() != claim_at_utc
        or parsed_at < parsed_claim_at
        or progress.get("postStartPredictionCreationAllowed") is not False
        or progress.get("immutablePredictionRewriteAllowed") is not False
        or progress.get("productionAuthorityChanged") is not False
        or re.fullmatch(r"[0-9a-f]{64}", progress_fingerprint) is None
        or progress_fingerprint
        != _source_pull_rebind_checkpoint_fingerprint(progress)
        or (
            stage == "PROVE_PRELOCK_ABSENCE"
            and "durableIdentity" in attempt
        )
    ):
        raise RuntimeError(
            "MLB_COOPERATIVE_TERMINAL_REVIEW_CHECKPOINT_CONTRACT_INVALID"
        )
    return copy.deepcopy(progress)

def _checkpoint_cooperative_replay(
    *,
    item: Dict[str, Any],
    owner: str,
    progress: Dict[str, Any],
    failed: bool,
    review_required: bool = False,
    review_stage: Optional[str] = None,
    review_error_code: Optional[str] = None,
) -> Dict[str, Any]:
    slate_date = str(item.get("slate_date_et") or "")
    request_id = str(item.get("request_id") or "")
    request_epoch = _nonnegative_receipt_integer(
        item.get("requested_at_epoch"), "request_epoch"
    )
    prior_progress = item.get("terminal_replay_progress")
    if review_required:
        if failed is not True:
            raise RuntimeError(
                "MLB_COOPERATIVE_TERMINAL_REVIEW_CHECKPOINT_CONTRACT_INVALID"
            )
        progress = _validated_cooperative_review_checkpoint_transition(
            item,
            progress,
            expected_stage=review_stage,
            expected_error_code=review_error_code,
        )
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

    source_pull_rebind_event_present = (
        COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_EVENT_FLAG in event
    )
    prelock_candidate_review_v2_event_present = (
        COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_EVENT_FLAG in event
    )
    if source_pull_rebind_event_present and (
        event.get(COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_EVENT_FLAG)
        is not True
        or set(event) != COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_EVENT_KEYS
        or not _is_cooperative_replay_request(event, method)
    ):
        return _failure_response(
            event,
            500,
            {
                "ok": False,
                "sport": "mlb",
                "error": (
                    "MLB_COOPERATIVE_TERMINAL_REPLAY_FAILED_CLOSED"
                ),
                "errorCode": (
                    "MLB_COOPERATIVE_SOURCE_PULL_REBIND_REVIEW_"
                    "EVENT_INVALID"
                ),
                "mutatingRunAttempted": False,
                "activeLeaseMutationAllowed": False,
                "postStartPredictionCreationAllowed": False,
                "immutablePredictionRewriteAllowed": False,
                "directWorkflowTableWrite": False,
            },
        )

    if prelock_candidate_review_v2_event_present and (
        event.get(COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_EVENT_FLAG)
        is not True
        or set(event)
        != COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_EVENT_KEYS
        or not _is_cooperative_replay_request(event, method)
    ):
        return _failure_response(
            event,
            500,
            {
                "ok": False,
                "sport": "mlb",
                "error": (
                    "MLB_COOPERATIVE_TERMINAL_REPLAY_FAILED_CLOSED"
                ),
                "errorCode": (
                    "MLB_COOPERATIVE_PRELOCK_CANDIDATE_REVIEW_V2_"
                    "EVENT_INVALID"
                ),
                "mutatingRunAttempted": False,
                "activeLeaseMutationAllowed": False,
                "postStartPredictionCreationAllowed": False,
                "immutablePredictionRewriteAllowed": False,
                "directWorkflowTableWrite": False,
            },
        )

    # A manually invoked exact historical repair never contests or mutates the
    # active execution lease.  It writes one bounded control-plane request and
    # polls that same record.  Only a normal EventBridge daily owner can claim
    # and execute it later under the existing lease.
    if _is_cooperative_replay_request(event, method):
        try:
            if prelock_candidate_review_v2_event_present:
                cooperative = (
                    _requeue_prelock_candidate_review_after_installed_runtime_proof_v2(
                        event
                    )
                )
            elif source_pull_rebind_event_present:
                cooperative = (
                    _requeue_source_pull_proof_review_after_rebind(event)
                )
            elif event.get("acknowledgeCooperativeCompletion") is True:
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
                            isinstance(progress, dict)
                            and chunk_result.get(
                                "checkpointWriteAllowed"
                            )
                            is True
                        ):
                            review_required = candidate_review_required
                            checkpointed = _checkpoint_cooperative_replay(
                                item=claimed,
                                owner=owner,
                                progress=progress,
                                failed=chunk_result.get("ok") is not True,
                                review_required=review_required,
                                review_stage=(
                                    chunk_stage
                                    if review_required
                                    else None
                                ),
                                review_error_code=(
                                    chunk_error_code
                                    if review_required
                                    else None
                                ),
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
