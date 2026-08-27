from __future__ import annotations

import copy
import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from botocore.exceptions import ClientError

VERSION = "MLB-IMMUTABLE-LOCKED-STORAGE-v5-persisted-stage-authority-chain"
SELECTION_TRAINING_SEPARATION_VERSION = "MLB-SELECTION-TRAINING-SEPARATION-v1"
AUTHORITY_VERSION = "MLB-CANONICAL-PER-GAME-STAGE-AUTHORITY-v2-persisted-chain"
UNAUTHORIZED_LOCKED_WRITE = "LOCKED_WRITE_REQUIRES_VERIFIED_IMMUTABLE_PER_GAME_STAGE"
REQUIRED_LOCK_MINUTES = 45
CANONICAL_READ_OVERLAY_IDEMPOTENCY_VERSION = (
    "MLB-CANONICAL-READ-OVERLAY-IDEMPOTENCY-v1"
)


def _tags(row: Dict[str, Any]) -> set[str]:
    return {str(value) for value in (row.get("tags") or [])}


def _locked(row: Dict[str, Any]) -> bool:
    tags = _tags(row)
    lock = row.get("slatePredictionLock") or row.get("lastPossiblePredictionGate") or {}
    audit = row.get("lockedCardAudit") or {}
    return bool(
        row.get("lockedPrediction") is True
        or row.get("officialPredictionStatus") == "OFFICIAL_LOCKED_PREDICTION"
        or audit.get("lockedFlag") is True
        or (isinstance(lock, dict) and (lock.get("locked") is True or lock.get("finalLocked") is True))
        or "FINAL_LOCKED" in tags
        or "SLATE_LOCKED" in tags
        or "OFFICIAL_LOCKED_PREDICTION" in tags
    )


def _slate(row: Dict[str, Any]) -> str:
    return str(row.get("slate_date") or row.get("slateDateEt") or "unknown")


def _identity(row: Dict[str, Any]) -> str:
    return str(row.get("gameIdentity") or row.get("gameId") or row.get("game_id") or row.get("id") or "unknown")


def _commence(row: Dict[str, Any]) -> str:
    return str(row.get("commenceTime") or row.get("commence_time") or "unknown")


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


def _canonical_identity(row: Dict[str, Any]) -> str:
    import mlb_slate_coverage_patch as coverage

    return coverage.game_identity(row)


def _stage_key(row: Dict[str, Any]) -> Dict[str, str]:
    digest = hashlib.sha256(_canonical_identity(row).encode("utf-8")).hexdigest()
    return {
        "PK": f"LOCKED_PICKS#mlb#{_slate(row)}",
        "SK": f"PER_GAME_LOCK#TMINUS{REQUIRED_LOCK_MINUTES}#{digest}",
    }


def _payload_fingerprint(value: Any) -> str:
    import mlb_daily_per_game_lock_patch as per_game

    return per_game._payload_fingerprint(value)


def _stage_fingerprint(item: Dict[str, Any]) -> str:
    import mlb_daily_per_game_lock_patch as per_game

    return per_game._stage_fingerprint(item)


def _stage_row_from_canonical(row: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(row)
    for key in (
        "canonicalPerGameStageAuthority",
        "immutableLockedStorageVersion",
        "immutableLockedStorage",
        "immutableLockedStorageKeyspace",
    ):
        out.pop(key, None)
    return out


def _stage_binding_errors(
    table: Any,
    stage: Dict[str, Any],
    row: Dict[str, Any],
    expected_key: Dict[str, str],
    *,
    canonical_row: bool,
) -> List[str]:
    import mlb_daily_per_game_lock_patch as per_game

    errors: List[str] = []
    if stage.get("PK") != expected_key["PK"] or stage.get("SK") != expected_key["SK"]:
        errors.append("stage_key_mismatch")
    if stage.get("record_type") != per_game.STAGE_RECORD_TYPE:
        errors.append("wrong_stage_record_type")
    if stage.get("model_version") != per_game.VERSION:
        errors.append("wrong_stage_model_version")
    if stage.get("lock_policy") != per_game.LOCK_POLICY:
        errors.append("wrong_stage_lock_policy")
    if stage.get("immutable_staged") is not True or stage.get("write_once") is not True:
        errors.append("stage_not_immutable_write_once")
    if stage.get("promotion_policy_version") != per_game.PROMOTION_POLICY_VERSION:
        errors.append("wrong_stage_promotion_policy")
    if stage.get("stage_fingerprint") != _stage_fingerprint(stage):
        errors.append("stage_fingerprint_mismatch")
    errors.extend(per_game.persisted_stage_authority_errors(table, stage))
    if str(stage.get("slate_date") or "") != _slate(row):
        errors.append("stage_slate_mismatch")
    if str(stage.get("game_identity") or "") != _canonical_identity(row):
        errors.append("stage_game_identity_mismatch")

    staged_row = ((stage.get("data") or {}).get("row") or {})
    if not isinstance(staged_row, dict) or not staged_row:
        errors.append("stage_row_missing")
        staged_row = {}
    compared_row = _stage_row_from_canonical(row) if canonical_row else copy.deepcopy(row)
    staged_fingerprint = _payload_fingerprint(staged_row) if staged_row else ""
    if not staged_fingerprint or staged_fingerprint != _payload_fingerprint(compared_row):
        errors.append("canonical_payload_not_exact_stage_row")

    proof = stage.get("candidate_proof") or {}
    if not isinstance(proof, dict) or proof.get("version") != per_game.PROMOTION_POLICY_VERSION:
        errors.append("candidate_proof_missing_or_wrong")
        proof = {}
    if proof.get("modelOrSignalRecomputedAtLock") is not False:
        errors.append("lock_time_rescore_not_disabled")
    for key in ("sourceAtOrBeforeCutoff", "createdAtOrBeforeCutoff", "persistedAtOrBeforeCutoff"):
        if proof.get(key) is not True:
            errors.append(f"candidate_proof_{key}_missing")
    if not proof.get("candidateSelectionFingerprint"):
        errors.append("candidate_selection_fingerprint_missing")
    for key in (
        "pk",
        "sk",
        "candidateRowFingerprint",
        "candidateSnapshotFingerprint",
        "predictionPayloadFingerprint",
        "snapshotVersion",
        "persistenceProofType",
        "persistenceWritePk",
        "persistenceWriteSk",
    ):
        if not proof.get(key):
            errors.append(f"candidate_proof_{key}_missing")
    if proof.get("promotionRule") != "last_valid_persisted_prediction_at_or_before_own_tminus45_becomes_final_lock":
        errors.append("candidate_promotion_rule_mismatch")
    if staged_row.get("lastPrelockSelectionFingerprint") != proof.get("candidateSelectionFingerprint"):
        errors.append("candidate_selection_fingerprint_mismatch")
    if staged_row.get("lastPrelockPromotionVersion") != per_game.PROMOTION_POLICY_VERSION:
        errors.append("stage_row_promotion_version_mismatch")
    if staged_row.get("modelOrSignalRecomputedAtLock") is not False:
        errors.append("stage_row_lock_time_rescore_not_disabled")
    if staged_row.get("immutablePerGameStage") is not True:
        errors.append("stage_row_authority_marker_missing")

    cutoff = _parse_dt(stage.get("scheduled_lock_at_utc"))
    source = _parse_dt(stage.get("source_pull_at_utc"))
    created = _parse_dt(proof.get("predictionCreatedAtUtc"))
    persisted = _parse_dt(proof.get("predictionPersistedAtUtc"))
    if not cutoff or not source or not created or not persisted:
        errors.append("stage_candidate_timestamps_missing")
    elif not (source <= created <= persisted <= cutoff):
        errors.append("stage_candidate_timestamps_not_prelock_ordered")
    row_cutoff = _parse_dt(
        staged_row.get("lockedAtUtc")
        or (staged_row.get("slatePredictionLock") or {}).get("lockAtUtc")
        or (staged_row.get("frozenFeatureVector") or {}).get("lockAtUtc")
    )
    if not cutoff or row_cutoff != cutoff:
        errors.append("stage_row_cutoff_mismatch")
    if str(stage.get("commence_time") or "") != _commence(staged_row):
        errors.append("stage_commence_time_mismatch")
    return sorted(set(errors))


def _read_verified_stage(table: Any, row: Dict[str, Any], *, canonical_row: bool) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    expected_key = _stage_key(row)
    cached = False
    stage = None
    try:
        import mlb_daily_per_game_lock_patch as per_game

        cached, stage = per_game._status_cached_item(table, expected_key)
    except Exception:
        cached = False
        stage = None
    if not cached:
        try:
            stage = table.get_item(Key=expected_key, ConsistentRead=True).get("Item")
        except Exception as exc:
            return None, [f"stage_consistent_read_failed:{type(exc).__name__}:{exc}"]
    if not isinstance(stage, dict):
        return None, ["verified_stage_not_found"]
    errors = _stage_binding_errors(table, stage, row, expected_key, canonical_row=canonical_row)
    return (stage if not errors else None), errors


def _authority_proof(stage: Dict[str, Any]) -> Dict[str, Any]:
    staged_row = ((stage.get("data") or {}).get("row") or {})
    candidate = stage.get("candidate_proof") or {}
    return {
        "version": AUTHORITY_VERSION,
        "verified": True,
        "consistentRead": True,
        "stagePk": stage.get("PK"),
        "stageSk": stage.get("SK"),
        "stageFingerprint": stage.get("stage_fingerprint"),
        "stageRowFingerprint": _payload_fingerprint(staged_row),
        "modelVersion": stage.get("model_version"),
        "lockPolicy": stage.get("lock_policy"),
        "promotionPolicyVersion": stage.get("promotion_policy_version"),
        "scheduledLockAtUtc": stage.get("scheduled_lock_at_utc"),
        "actualStagedAtUtc": stage.get("staged_at_utc"),
        "sourceWindowVersion": (stage.get("source_window") or {}).get("version"),
        "providerManifestFingerprint": (stage.get("provider_manifest_authority") or {}).get("fingerprint"),
        "providerManifestPk": (stage.get("provider_manifest_authority") or {}).get("pk"),
        "providerManifestSk": (stage.get("provider_manifest_authority") or {}).get("sk"),
        "candidateSnapshotPk": candidate.get("pk"),
        "candidateSnapshotSk": candidate.get("sk"),
        "candidateSnapshotFingerprint": candidate.get("candidateSnapshotFingerprint"),
        "candidateSelectionFingerprint": candidate.get("candidateSelectionFingerprint"),
        "modelOrSignalRecomputedAtLock": False,
    }


def validate_canonical_stage_authority(table: Any, row: Dict[str, Any]) -> List[str]:
    proof = row.get("canonicalPerGameStageAuthority") or {}
    errors: List[str] = []
    if not isinstance(proof, dict) or proof.get("version") != AUTHORITY_VERSION or proof.get("verified") is not True:
        errors.append("canonical_stage_authority_proof_missing")
        proof = {}
    stage, stage_errors = _read_verified_stage(table, row, canonical_row=True)
    errors.extend(stage_errors)
    if stage:
        expected = _authority_proof(stage)
        for key, value in expected.items():
            if proof.get(key) != value:
                errors.append(f"canonical_stage_authority_{key}_mismatch")
    return sorted(set(errors))


def _locked_item(module: Any, row: Dict[str, Any]) -> Dict[str, Any]:
    row["immutableLockedStorageVersion"] = VERSION
    row["immutableLockedStorage"] = True
    row["immutableLockedStorageKeyspace"] = "LOCKED#GAME"
    vector_errors = _vector_errors(row)
    training = row.get("mlFeatureFreeze") or {}
    return module.history.ddb_safe({
        "PK": f"GAME_WINNERS#mlb#{_slate(row)}",
        "SK": f"LOCKED#GAME#{_commence(row)}#{_identity(row)}",
        "record_type": "mlb_immutable_locked_single_game_prediction",
        "sport": "mlb",
        "slate_date": _slate(row),
        "game_id": row.get("gameId") or row.get("game_id") or row.get("id"),
        "game_identity": row.get("gameIdentity") or _identity(row),
        "game_key": row.get("gameKey"),
        "predicted_winner": row.get("predictedWinner"),
        "confidence_tier": row.get("confidenceTier"),
        "promotion_status": row.get("promotionStatus"),
        "promoted": row.get("promoted"),
        "score": row.get("score"),
        "win_probability": row.get("winProbability"),
        "edge_vs_book": row.get("edgeVsBook"),
        "expected_value": row.get("expectedValue"),
        "created_at": row.get("createdAt") or row.get("created_at"),
        "immutable_locked": True,
        "stage_authority_verified": True,
        "stage_authority_version": AUTHORITY_VERSION,
        "stage_fingerprint": (row.get("canonicalPerGameStageAuthority") or {}).get("stageFingerprint"),
        "immutable_locked_storage_version": VERSION,
        "selection_lock_verified": True,
        "exact_vector_verified": not vector_errors,
        "training_eligible": bool(training.get("trainingEligible")),
        "training_exclusion_reasons": list(training.get("trainingExclusionReasons") or []),
        "data": row,
    })


def _vector_errors(row: Dict[str, Any]) -> List[str]:
    try:
        import mlb_daily_lock_ml_vector_preservation_patch as vector_contract
        return vector_contract.effective_selection_lock_vector_errors(row)
    except Exception as exc:
        return [f"exact_vector_validator_unavailable:{exc}"]


def _require_vector_status(row: Dict[str, Any], *, context: str) -> List[str]:
    try:
        import mlb_daily_lock_ml_vector_preservation_patch as vector_contract

        status_errors = vector_contract.validate_selection_lock_vector_status(row)
    except Exception as exc:
        status_errors = [f"selection_vector_status_validator_unavailable:{exc}"]
    if status_errors:
        game_id = _identity(row)
        raise RuntimeError(
            f"MLB_IMMUTABLE_LOCKED_VECTOR_STATUS_REJECTED:{context}:{game_id}:"
            + ",".join(sorted(set(status_errors)))
        )
    return _vector_errors(row)


def _stored_row(item: Dict[str, Any]) -> Dict[str, Any]:
    data = item.get("data") or {}
    return copy.deepcopy(data) if isinstance(data, dict) else {}


def _fingerprint(row: Dict[str, Any]) -> str:
    vector = row.get("frozenFeatureVector") or {}
    return str(vector.get("fingerprint") or "")


def _vector_identity(row: Dict[str, Any]) -> Dict[str, Any]:
    """Bind the complete frozen vector, not only its self-reported digest."""

    return {
        "frozenFeatureVector": copy.deepcopy(
            row.get("frozenFeatureVector") or {}
        ),
        "frozenFeatureVectorVersion": row.get(
            "frozenFeatureVectorVersion"
        ),
        "featureVectorFrozenAtLock": row.get(
            "featureVectorFrozenAtLock"
        ),
        "frozenOutcomeFeatures": copy.deepcopy(
            row.get("frozenOutcomeFeatures")
        ),
        "frozenReliabilityFeatures": copy.deepcopy(
            row.get("frozenReliabilityFeatures")
        ),
        "mlFeatureFreeze": copy.deepcopy(
            row.get("mlFeatureFreeze") or {}
        ),
        "exactVectorVerified": row.get("exactVectorVerified"),
        "exactVectorValidationErrors": list(
            row.get("exactVectorValidationErrors") or []
        ),
        "selectionTrainingSeparationVersion": row.get(
            "selectionTrainingSeparationVersion"
        ),
    }


def _locked_key(row: Dict[str, Any]) -> Dict[str, str]:
    return {
        "PK": f"GAME_WINNERS#mlb#{_slate(row)}",
        "SK": f"LOCKED#GAME#{_commence(row)}#{_identity(row)}",
    }


def _canonical_read_overlay(row: Dict[str, Any]) -> bool:
    """Recognize only the validated public view of an existing canonical row."""

    try:
        import mlb_slate_coverage_patch as coverage
    except Exception:
        return False

    per_game = row.get("perGameCanonicalLock") or {}
    public_lock = row.get("slatePredictionLock") or {}
    final_gate = row.get("lastPossiblePredictionGate") or {}
    authority = row.get("canonicalPerGameStageAuthority") or {}
    row_lock_at = _parse_dt(
        row.get("lockedAtUtc")
        or (row.get("frozenFeatureVector") or {}).get("lockAtUtc")
    )
    per_game_lock_at = _parse_dt(
        per_game.get("lockAtUtc")
        if isinstance(per_game, dict)
        else None
    )
    gate_lock_at = _parse_dt(
        final_gate.get("lockAtUtc")
        if isinstance(final_gate, dict)
        else None
    )
    public_status = (
        str(public_lock.get("lockStatus") or "")
        if isinstance(public_lock, dict)
        else ""
    )
    public_locked = (
        public_lock.get("locked")
        if isinstance(public_lock, dict)
        else None
    )
    public_status_consistent = bool(
        public_status == "OFFICIAL_LOCKED_PREDICTION"
        and public_locked is True
        and _parse_dt(public_lock.get("lockAtUtc")) == row_lock_at
    )
    required_tags = {
        "FINAL_LOCKED",
        "OFFICIAL_PREDICTION",
        "OFFICIAL_LOCKED_PREDICTION",
        "CANONICAL_PER_GAME_LOCK",
    }
    conflicting_tags = {
        "PRE_LOCK_PREDICTION",
        "PER_GAME_CANONICAL_LOCK_PENDING",
        "LOCKED_NO_PREDICTION_DATA",
        "MISSED_LOCK",
        "SLATE_WIDE_45_MIN_LOCK_POLICY",
    }
    tags = _tags(row)
    return bool(
        row.get("slateCoverageVersion") == coverage.VERSION
        and row.get("officialPredictionReason")
        == "validated_immutable_canonical_per_game_lock"
        and row.get("immutablePerGameStage") is True
        and row.get("immutableLockedStorage") is True
        and row.get("immutableLockedStorageKeyspace") == "LOCKED#GAME"
        and row.get("immutableLockedStorageVersion") == VERSION
        and row.get("canonical") is True
        and row.get("locked") is True
        and row.get("lockedPrediction") is True
        and row.get("officialPrediction") is True
        and row.get("officialPick") is True
        and row.get("isOfficialDisplayPick") is True
        and row.get("lockOutcomeRecorded") is True
        and row.get("lockStatus") == "LOCKED_CANONICAL"
        and row.get("officialPredictionStatus")
        == "OFFICIAL_LOCKED_PREDICTION"
        and row.get("selectionFingerprint")
        == row.get("lastPrelockSelectionFingerprint")
        and bool(row.get("lastPrelockSelectionFingerprint"))
        and row_lock_at is not None
        and _parse_dt(row.get("scheduledLockAtUtc")) == row_lock_at
        and required_tags.issubset(tags)
        and not conflicting_tags.intersection(tags)
        and per_game_lock_at == row_lock_at
        and gate_lock_at == row_lock_at
        and isinstance(per_game, dict)
        and per_game
        == {
            "authorityVersion": coverage.AUTHORITY_VERSION,
            "status": "OFFICIAL_LOCKED_PREDICTION",
            "lockAtUtc": row.get("lockedAtUtc"),
            "canonical": True,
        }
        and isinstance(final_gate, dict)
        and final_gate.get("policyVersion") == coverage.AUTHORITY_VERSION
        and final_gate.get("phase") == "FINAL_LOCKED"
        and final_gate.get("finalWindowActive") is False
        and final_gate.get("finalLocked") is True
        and final_gate.get("perGameLock") is True
        and final_gate.get("slateWideLock") is False
        and isinstance(public_lock, dict)
        and public_lock.get("policyVersion") == coverage.AUTHORITY_VERSION
        and public_lock.get("authorityVersion") == coverage.AUTHORITY_VERSION
        and public_lock.get("canonicalReadOperational") is True
        and public_lock.get("perGameLock") is True
        and public_lock.get("slateWideLock") is False
        and public_status_consistent
        and isinstance(authority, dict)
        and authority.get("version") == AUTHORITY_VERSION
        and authority.get("verified") is True
        and authority.get("consistentRead") is True
    )


_CANONICAL_OVERLAY_PUBLIC_FIELDS = frozenset(
    {
        "actionablePick",
        "blocked",
        "canonical",
        "eventPlayabilityAssessmentRequired",
        "historicalPlayabilityAssessmentValidationErrors",
        "isOfficialDisplayPick",
        "lastPossiblePredictionGate",
        "lockOutcomeRecorded",
        "locked",
        "lockedPrediction",
        "lockStatus",
        "officialPick",
        "officialPrediction",
        "officialPredictionReason",
        "officialPredictionStatus",
        "perGameCanonicalLock",
        "playabilityAssessment",
        "playabilityAssessmentValidationErrors",
        "playabilityBlockReasons",
        "playabilityStatus",
        "playable",
        "playablePick",
        "readiness",
        "readinessValidationErrors",
        "releaseBlockReasons",
        "releaseBlocked",
        "requiredPlayabilityCheckpoint",
        "requiredPlayabilityCheckpointDue",
        "requiredReadinessCheckpoint",
        "requiredReadinessCheckpointDue",
        "scheduledLockAtUtc",
        "selectionFingerprint",
        "slateCoverageVersion",
        "slatePredictionLock",
        "tags",
        "trainingEligibilityStatus",
        "trainingEligible",
        "trainingExclusionReasons",
        "wagerReleaseBlocked",
    }
)


def _immutable_overlay_base(row: Dict[str, Any]) -> Dict[str, Any]:
    """Strip only fields that the canonical public renderer owns."""

    out = copy.deepcopy(row)
    for field in _CANONICAL_OVERLAY_PUBLIC_FIELDS:
        out.pop(field, None)
    return out


_SLATE_LOCK_RENDERER_FIELDS = frozenset(
    {
        "authorityVersion",
        "lockAtUtc",
        "locked",
        "lockStatus",
        "perGameLock",
        "policyVersion",
        "slateWideLock",
    }
)

# Keys produced by _manifest_lock_state, provider-manifest authority, and the
# aggregate public lock summary. They are observability overlays, not stored
# per-game stage material.
_SLATE_LOCK_PUBLIC_OBSERVABILITY_FIELDS = frozenset(
    {
        "applied",
        "canonicalCoverageComplete",
        "canonicalLockedGameCount",
        "canonicalPredictionComplete",
        "canonicalPredictionCount",
        "canonicalReadError",
        "canonicalReadOperational",
        "doubleheaderSafeIdentity",
        "durableRosterImmutableReadbackVerified",
        "eventRosterBacked",
        "firstGameStartUtc",
        "firstPerGameLockAtUtc",
        "invalidCanonicalRows",
        "invalidLifecycleStatusRows",
        "invalidPlayabilityReleaseRows",
        "invalidTerminalLifecycleRows",
        "lastGameStartUtc",
        "lastPerGameLockAtUtc",
        "latestAvailablePullAt",
        "latestProviderFeedAnomalies",
        "latestProviderFeedAnomalyCount",
        "latestProviderFeedContracted",
        "latestProviderFeedGameCount",
        "latestProviderManifestFingerprint",
        "latestProviderManifestObservedAtUtc",
        "latestScoringPullAt",
        "legacyRosterMigrationFallback",
        "lockDueCanonicalMissingCount",
        "lockMinutesBeforeEachGame",
        "lockMinutesBeforeFirstGame",
        "lockOutcomeCount",
        "lockOutcomeCoveragePct",
        "lockStatusComplete",
        "lockedPredictionCount",
        "lockedStatusCount",
        "manifestGameCount",
        "manifestGameIdentities",
        "manifestVersion",
        "minutesUntilFirstGameStart",
        "minutesUntilFirstPerGameLock",
        "missedLockCount",
        "noPredictionDataCount",
        "officialScheduleAuthoritativeStartTimes",
        "officialScheduleAuthorityFingerprint",
        "officialScheduleAuthoritySource",
        "officialScheduleAuthorityVersion",
        "officialScheduleBacked",
        "officialScheduleGameCount",
        "officialScheduleMissingProviderEventGameIds",
        "operationalDefectScopeVersion",
        "operationalDefectScopes",
        "pendingCanonicalGameCount",
        "pendingCanonicalStatuses",
        "pendingLockStatusGameCount",
        "providerManifestFingerprint",
        "providerManifestFullProviderSchedule",
        "providerManifestImmutable",
        "providerManifestObservedAtUtc",
        "providerManifestPk",
        "providerManifestPullId",
        "providerManifestSk",
        "providerManifestValidated",
        "providerManifestVersion",
        "readinessValidationWarnings",
        "readinessWarningGameCount",
        "releasePlayabilityOperationalDefect",
        "rosterAuthorityMode",
        "rules",
        "scoringPullCount",
        "source",
        "totalPullCountAvailable",
        "verifiedFullSlateGameCount",
        "verifiedFullSlateManifestVersion",
        "winnerLifecycleOperationalDefect",
    }
)


def _public_lock_binding_errors(
    existing_row: Dict[str, Any],
    incoming_row: Dict[str, Any],
) -> List[str]:
    """Bind every stored slate-lock field that _official_row preserves."""

    existing_lock = existing_row.get("slatePredictionLock") or {}
    incoming_lock = incoming_row.get("slatePredictionLock") or {}
    if not isinstance(existing_lock, dict) or not isinstance(
        incoming_lock, dict
    ):
        return ["canonical_overlay_public_lock_not_mapping"]

    preserved = {
        key: copy.deepcopy(value)
        for key, value in existing_lock.items()
        if key not in _SLATE_LOCK_RENDERER_FIELDS
    }
    missing = sorted(set(preserved) - set(incoming_lock))
    mismatched = sorted(
        key
        for key, value in preserved.items()
        if key in incoming_lock
        and _payload_fingerprint(incoming_lock.get(key))
        != _payload_fingerprint(value)
    )
    allowed = (
        set(preserved)
        | set(_SLATE_LOCK_RENDERER_FIELDS)
        | set(_SLATE_LOCK_PUBLIC_OBSERVABILITY_FIELDS)
    )
    extra = sorted(set(incoming_lock) - allowed)
    errors = []
    if missing:
        errors.append(
            "canonical_overlay_public_lock_preserved_fields_missing:"
            + ",".join(missing)
        )
    if mismatched:
        errors.append(
            "canonical_overlay_public_lock_preserved_fields_mismatch:"
            + ",".join(mismatched)
        )
    if extra:
        errors.append(
            "canonical_overlay_public_lock_unknown_fields:"
            + ",".join(extra)
        )
    return errors


def _rendered_tag_binding_errors(
    existing_row: Dict[str, Any],
    incoming_row: Dict[str, Any],
) -> List[str]:
    """Reconstruct the exact official/playability tag overlay."""

    raw_tags = incoming_row.get("tags")
    if not isinstance(raw_tags, list):
        return ["canonical_overlay_rendered_tags_not_list"]
    actual = [str(tag) for tag in raw_tags]
    errors = []
    if len(actual) != len(set(actual)):
        errors.append("canonical_overlay_rendered_tags_duplicate")

    expected = {
        str(tag)
        for tag in (existing_row.get("tags") or [])
        if str(tag) != "SLATE_WIDE_45_MIN_LOCK_POLICY"
    }
    expected.update(
        {
            "CANONICAL_PER_GAME_LOCK",
            "FINAL_LOCKED",
            "OFFICIAL_LOCKED_PREDICTION",
            "OFFICIAL_PREDICTION",
        }
    )
    if incoming_row.get("playable") is True:
        expected.update({"ACTIONABLE_PICK", "PLAYABLE_PREDICTION"})
        expected.difference_update(
            {"NOT_PLAYABLE", "RELEASE_BLOCKED", "WAGER_RELEASE_BLOCKED"}
        )
    else:
        expected.update(
            {"NOT_PLAYABLE", "RELEASE_BLOCKED", "WAGER_RELEASE_BLOCKED"}
        )
        expected.difference_update(
            {"ACTIONABLE_PICK", "PLAYABLE_PREDICTION"}
        )
    if actual != sorted(expected):
        errors.append("canonical_overlay_rendered_tags_mismatch")
    return errors


def _canonical_overlay_projection(row: Dict[str, Any]) -> Dict[str, Any]:
    """Return immutable selection/stage material unaffected by public overlays."""

    import mlb_daily_per_game_lock_patch as per_game

    vector = row.get("frozenFeatureVector") or {}
    authority = row.get("canonicalPerGameStageAuthority") or {}
    lock_at = (
        row.get("lockedAtUtc")
        or vector.get("lockAtUtc")
        or (row.get("slatePredictionLock") or {}).get("lockAtUtc")
    )
    selection_material = per_game._selection_material(row)
    final_gate = copy.deepcopy(
        row.get("lastPossiblePredictionGate") or {}
    )
    for field in (
        "policyVersion",
        "phase",
        "finalWindowActive",
        "finalLocked",
        "slateWideLock",
        "perGameLock",
        "lockAtUtc",
    ):
        final_gate.pop(field, None)
    return {
        "immutableStoredRow": _immutable_overlay_base(row),
        "slate": _slate(row),
        "canonicalGameIdentity": _canonical_identity(row),
        "storageGameIdentity": _identity(row),
        "commenceTime": _commence(row),
        "gameId": row.get("gameId") or row.get("game_id"),
        "gameKey": row.get("gameKey"),
        "officialGamePk": row.get("officialGamePk"),
        "officialGameId": row.get("officialGameId"),
        "sourcePredictionGameId": row.get("sourcePredictionGameId"),
        "sourcePredictionGameIdentity": row.get(
            "sourcePredictionGameIdentity"
        ),
        "homeTeam": row.get("homeTeam") or row.get("home_team"),
        "awayTeam": row.get("awayTeam") or row.get("away_team"),
        "predictedWinner": row.get("predictedWinner"),
        "predictedSide": row.get("predictedSide"),
        "confidenceTier": row.get("confidenceTier"),
        "promotionStatus": row.get("promotionStatus"),
        "promoted": row.get("promoted"),
        "score": row.get("score"),
        "winProbability": row.get("winProbability"),
        "edgeVsBook": row.get("edgeVsBook"),
        "expectedValue": row.get("expectedValue"),
        "createdAt": row.get("createdAt") or row.get("created_at"),
        "lastPrelockPromotionVersion": row.get(
            "lastPrelockPromotionVersion"
        ),
        "lastPrelockSelectionFingerprint": row.get(
            "lastPrelockSelectionFingerprint"
        ),
        "recomputedSelectionFingerprint": _payload_fingerprint(
            selection_material
        ),
        "lockedAtUtc": (
            _parse_dt(lock_at).isoformat() if _parse_dt(lock_at) else None
        ),
        "predictionSourcePullAt": (
            _parse_dt(row.get("predictionSourcePullAt")).isoformat()
            if _parse_dt(row.get("predictionSourcePullAt"))
            else None
        ),
        "predictionSourcePullId": row.get("predictionSourcePullId"),
        "sourceLockLatestScoringPullAt": (
            _parse_dt(
                (row.get("slatePredictionLock") or {}).get(
                    "latestScoringPullAt"
                )
            ).isoformat()
            if _parse_dt(
                (row.get("slatePredictionLock") or {}).get(
                    "latestScoringPullAt"
                )
            )
            else None
        ),
        "finalGateImmutableBase": final_gate,
        "publicTrainingEligible": row.get("trainingEligible"),
        "publicTrainingEligibilityStatus": row.get(
            "trainingEligibilityStatus"
        ),
        "publicTrainingExclusionReasons": list(
            row.get("trainingExclusionReasons") or []
        ),
        "modelOrSignalRecomputedAtLock": row.get(
            "modelOrSignalRecomputedAtLock"
        ),
        "canonicalPerGameStageAuthority": copy.deepcopy(authority),
        "frozenFeatureVectorIdentity": _vector_identity(row),
    }


def _read_existing_canonical_stage_direct(
    table: Any,
    row: Dict[str, Any],
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """Strong-read and validate the raw stage behind a canonical stored row."""

    key = _stage_key(row)
    try:
        stage = table.get_item(
            Key=key,
            ConsistentRead=True,
        ).get("Item")
    except Exception as exc:
        return None, [
            "canonical_overlay_stage_consistent_read_failed:"
            f"{type(exc).__name__}:{exc}"
        ]
    if not isinstance(stage, dict):
        return None, ["canonical_overlay_verified_stage_not_found"]

    errors = _stage_binding_errors(
        table,
        stage,
        row,
        key,
        canonical_row=True,
    )
    proof = row.get("canonicalPerGameStageAuthority") or {}
    expected = _authority_proof(stage)
    if not isinstance(proof, dict):
        errors.append(
            "canonical_overlay_existing_stage_authority_proof_missing"
        )
        proof = {}
    if set(proof) != set(expected):
        missing = sorted(set(expected) - set(proof))
        extra = sorted(set(proof) - set(expected))
        errors.append(
            "canonical_overlay_existing_stage_authority_keyset_mismatch:"
            f"missing={','.join(missing)};extra={','.join(extra)}"
        )
    for field, value in expected.items():
        if proof.get(field) != value:
            errors.append(
                "canonical_overlay_existing_stage_authority_"
                f"{field}_mismatch"
            )
    return (stage if not errors else None), sorted(set(errors))


def _existing_envelope_binding_errors(
    module: Any,
    existing: Dict[str, Any],
    existing_row: Dict[str, Any],
) -> List[str]:
    """Bind every redundant LOCKED#GAME envelope field back to stored data."""

    try:
        expected = _locked_item(
            module,
            copy.deepcopy(existing_row),
        )
    except Exception as exc:
        return [
            "canonical_overlay_existing_envelope_rebuild_failed:"
            f"{type(exc).__name__}:{exc}"
        ]
    expected_keys = set(expected)
    actual_keys = set(existing)
    if actual_keys != expected_keys:
        missing = sorted(expected_keys - actual_keys)
        extra = sorted(actual_keys - expected_keys)
        return [
            "canonical_overlay_existing_envelope_keyset_mismatch:"
            f"missing={','.join(missing)};extra={','.join(extra)}"
        ]
    expected_envelope = {
        key: copy.deepcopy(value)
        for key, value in expected.items()
        if key != "data"
    }
    actual_envelope = {
        key: copy.deepcopy(existing.get(key))
        for key in expected_envelope
    }
    if _payload_fingerprint(actual_envelope) != _payload_fingerprint(
        expected_envelope
    ):
        mismatches = sorted(
            key
            for key, value in expected_envelope.items()
            if _payload_fingerprint(actual_envelope.get(key))
            != _payload_fingerprint(value)
        )
        return [
            "canonical_overlay_existing_envelope_mismatch:"
            + ",".join(mismatches)
        ]
    return []


def _canonical_overlay_rejection(
    key: Dict[str, str],
    errors: List[str],
) -> Dict[str, Any]:
    return {
        "ok": False,
        "stored": False,
        "suppressed": True,
        "error": "LOCKED_CANONICAL_READ_OVERLAY_EXISTING_VERIFICATION_FAILED",
        "authorityErrors": sorted(set(errors)),
        "pk": key["PK"],
        "sk": key["SK"],
        "storageClass": "LOCKED_REJECTED",
        "canonicalWriteAuthorized": False,
        "canonicalWriteAttempted": False,
        "canonicalReadOverlayVerified": False,
        "requiredAuthority": (
            "strongly verified existing immutable LOCKED#GAME row "
            "with exact stage and immutable selection binding"
        ),
        "idempotencyVersion": (
            CANONICAL_READ_OVERLAY_IDEMPOTENCY_VERSION
        ),
        "version": VERSION,
    }


def _verify_existing_canonical_overlay(
    module: Any,
    row: Dict[str, Any],
) -> Dict[str, Any]:
    """Strong-read an existing canonical overlay without attempting a rewrite."""

    table = module.history.PULLS
    key = _locked_key(row)
    try:
        existing = table.get_item(
            Key=key,
            ConsistentRead=True,
        ).get("Item")
    except Exception as exc:
        return _canonical_overlay_rejection(
            key,
            [
                "canonical_overlay_consistent_read_failed:"
                f"{type(exc).__name__}:{exc}"
            ],
        )

    errors: List[str] = []
    if not isinstance(existing, dict):
        return _canonical_overlay_rejection(
            key,
            ["canonical_overlay_existing_locked_item_missing"],
        )
    if existing.get("PK") != key["PK"] or existing.get("SK") != key["SK"]:
        errors.append("canonical_overlay_existing_key_mismatch")
    if existing.get("record_type") != (
        "mlb_immutable_locked_single_game_prediction"
    ):
        errors.append("canonical_overlay_existing_record_type_mismatch")
    if existing.get("immutable_locked") is not True:
        errors.append("canonical_overlay_existing_immutable_flag_missing")
    if existing.get("stage_authority_verified") is not True:
        errors.append(
            "canonical_overlay_existing_stage_authority_flag_missing"
        )
    if existing.get("stage_authority_version") != AUTHORITY_VERSION:
        errors.append(
            "canonical_overlay_existing_stage_authority_version_mismatch"
        )
    if existing.get("immutable_locked_storage_version") != VERSION:
        errors.append(
            "canonical_overlay_existing_storage_version_mismatch"
        )
    if existing.get("selection_lock_verified") is not True:
        errors.append(
            "canonical_overlay_existing_selection_verification_missing"
        )

    existing_row = _stored_row(existing)
    if not existing_row:
        errors.append("canonical_overlay_existing_data_missing")
    else:
        if existing_row.get("immutablePerGameStage") is not True:
            errors.append(
                "canonical_overlay_existing_stage_marker_missing"
            )
        if existing_row.get("immutableLockedStorage") is not True:
            errors.append(
                "canonical_overlay_existing_locked_storage_marker_missing"
            )
        if existing_row.get("immutableLockedStorageKeyspace") != (
            "LOCKED#GAME"
        ):
            errors.append(
                "canonical_overlay_existing_locked_keyspace_mismatch"
            )
        if existing_row.get("immutableLockedStorageVersion") != VERSION:
            errors.append(
                "canonical_overlay_existing_row_storage_version_mismatch"
            )
        errors.extend(
            _public_lock_binding_errors(
                existing_row,
                row,
            )
        )
        errors.extend(
            _rendered_tag_binding_errors(
                existing_row,
                row,
            )
        )
        errors.extend(
            _existing_envelope_binding_errors(
                module,
                existing,
                existing_row,
            )
        )
        stage, stage_errors = _read_existing_canonical_stage_direct(
            table,
            existing_row,
        )
        errors.extend(stage_errors)
        try:
            existing_vector_errors = _require_vector_status(
                existing_row,
                context="existing_canonical_overlay",
            )
        except Exception as exc:
            existing_vector_errors = []
            errors.append(
                "canonical_overlay_existing_vector_status_invalid:"
                f"{type(exc).__name__}:{exc}"
            )
        try:
            incoming_vector_errors = _require_vector_status(
                row,
                context="canonical_read_overlay",
            )
        except Exception as exc:
            incoming_vector_errors = []
            errors.append(
                "canonical_overlay_vector_status_invalid:"
                f"{type(exc).__name__}:{exc}"
            )
        if sorted(set(existing_vector_errors)) != sorted(
            set(incoming_vector_errors)
        ):
            errors.append(
                "canonical_overlay_vector_error_set_mismatch"
            )
        try:
            if _payload_fingerprint(
                _canonical_overlay_projection(row)
            ) != _payload_fingerprint(
                _canonical_overlay_projection(existing_row)
            ):
                errors.append(
                    "canonical_overlay_immutable_projection_mismatch"
                )
        except Exception as exc:
            errors.append(
                "canonical_overlay_projection_validation_failed:"
                f"{type(exc).__name__}:{exc}"
            )

    if errors:
        return _canonical_overlay_rejection(key, errors)

    existing_training = existing_row.get("mlFeatureFreeze") or {}
    authority = existing_row.get("canonicalPerGameStageAuthority") or {}
    return {
        "ok": True,
        "pk": key["PK"],
        "sk": key["SK"],
        "storageClass": "LOCKED_IMMUTABLE",
        "writeOnce": True,
        "created": False,
        "immutableExisting": True,
        "idempotentExistingVerified": True,
        "canonicalWriteAttempted": False,
        "canonicalReadOverlayVerified": True,
        "selectionLockVerified": True,
        "exactVectorVerified": not existing_vector_errors,
        "exactVectorValidationErrors": existing_vector_errors,
        "incomingExactVectorValidationErrors": incoming_vector_errors,
        "trainingEligible": bool(
            existing_training.get("trainingEligible")
        ),
        "trainingExclusionReasons": list(
            existing_training.get("trainingExclusionReasons") or []
        ),
        "stageAuthorityVerified": True,
        "stageAuthorityVersion": AUTHORITY_VERSION,
        "stageFingerprint": stage.get("stage_fingerprint"),
        "frozenFeatureVectorFingerprint": _fingerprint(existing_row),
        "idempotencyVersion": (
            CANONICAL_READ_OVERLAY_IDEMPOTENCY_VERSION
        ),
        "version": VERSION,
    }


def apply(module: Any):
    if getattr(module, "_INQSI_MLB_IMMUTABLE_LOCKED_STORAGE_APPLIED", False):
        return module

    original_store = module._store_prediction

    def store_prediction(row: Dict[str, Any]) -> Dict[str, Any]:
        if not _locked(row):
            stored = original_store(row)
            if isinstance(stored, dict):
                stored = dict(stored)
                if stored.get("ok") is True:
                    stored["storageClass"] = "LIVE_MUTABLE"
                else:
                    stored.setdefault("storageClass", "PREGAME_REJECTED")
                stored["immutableLockedStorageVersion"] = VERSION
            return stored

        # A locked-looking row produced by a legacy slate gate or a generic
        # predict_all(store=True) call is not canonical authority.  The marker
        # is only a routing hint: the same table must prove the exact immutable
        # T-minus-45 stage with a strongly consistent read before this write.
        if row.get("immutablePerGameStage") is not True:
            return {
                "ok": False,
                "stored": False,
                "suppressed": True,
                "error": UNAUTHORIZED_LOCKED_WRITE,
                "storageClass": "LOCKED_REJECTED",
                "canonicalWriteAuthorized": False,
                "requiredAuthority": "verified immutable T-minus-45 stage record",
                "version": VERSION,
            }

        if module.history.PULLS is None:
            return {"ok": False, "error": "SNAPSHOTS_TABLE not configured", "storageClass": "LOCKED_IMMUTABLE"}

        # The public coverage authority may return a display-enriched view of
        # a canonical row that is already durable.  It is not the exact raw
        # T-minus-45 stage and must never be rewritten.  Verify the existing
        # LOCKED#GAME record and its immutable projection instead.
        if _canonical_read_overlay(row):
            return _verify_existing_canonical_overlay(module, row)

        stage, stage_errors = _read_verified_stage(module.history.PULLS, row, canonical_row=False)
        if not stage or stage_errors:
            return {
                "ok": False,
                "stored": False,
                "suppressed": True,
                "error": UNAUTHORIZED_LOCKED_WRITE,
                "authorityErrors": stage_errors,
                "storageClass": "LOCKED_REJECTED",
                "canonicalWriteAuthorized": False,
                "requiredAuthority": "verified immutable T-minus-45 stage record",
                "version": VERSION,
            }

        vector_errors = _require_vector_status(row, context="new_write")
        row["canonicalPerGameStageAuthority"] = _authority_proof(stage)
        item = _locked_item(module, row)
        training = row.get("mlFeatureFreeze") or {}
        try:
            module.history.PULLS.put_item(
                Item=item,
                ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
            )
            return {
                "ok": True,
                "pk": item["PK"],
                "sk": item["SK"],
                "storageClass": "LOCKED_IMMUTABLE",
                "writeOnce": True,
                "created": True,
                "selectionLockVerified": True,
                "exactVectorVerified": not vector_errors,
                "exactVectorValidationErrors": vector_errors,
                "trainingEligible": bool(training.get("trainingEligible")),
                "trainingExclusionReasons": list(training.get("trainingExclusionReasons") or []),
                "stageAuthorityVerified": True,
                "stageAuthorityVersion": AUTHORITY_VERSION,
                "stageFingerprint": stage.get("stage_fingerprint"),
                "frozenFeatureVectorFingerprint": _fingerprint(row),
                "version": VERSION,
            }
        except ClientError as exc:
            code = str((exc.response.get("Error") or {}).get("Code") or "")
            if code != "ConditionalCheckFailedException":
                raise
            existing = module.history.PULLS.get_item(
                Key={"PK": item["PK"], "SK": item["SK"]},
                ConsistentRead=True,
            ).get("Item")
            if not existing:
                raise
            existing_row = _stored_row(existing)
            existing_vector_errors = _require_vector_status(
                existing_row,
                context="existing_collision",
            )
            existing_authority_errors = validate_canonical_stage_authority(
                module.history.PULLS,
                existing_row,
            )
            if existing_authority_errors:
                raise RuntimeError(
                    "MLB_IMMUTABLE_LOCKED_EXISTING_STAGE_AUTHORITY_REJECTED:"
                    + ",".join(existing_authority_errors)
                )
            existing_fingerprint = _fingerprint(existing_row)
            if _payload_fingerprint(item.get("data") or {}) != _payload_fingerprint(existing_row):
                raise RuntimeError(
                    "MLB_IMMUTABLE_LOCKED_PAYLOAD_COLLISION_MISMATCH:"
                    f"{_identity(row)}"
                )
            existing_training = existing_row.get("mlFeatureFreeze") or {}
            return {
                "ok": True,
                "pk": item["PK"],
                "sk": item["SK"],
                "storageClass": "LOCKED_IMMUTABLE",
                "writeOnce": True,
                "created": False,
                "immutableExisting": True,
                "selectionLockVerified": True,
                "exactVectorVerified": not existing_vector_errors,
                "exactVectorValidationErrors": existing_vector_errors,
                "trainingEligible": bool(existing_training.get("trainingEligible")),
                "trainingExclusionReasons": list(existing_training.get("trainingExclusionReasons") or []),
                "stageAuthorityVerified": True,
                "stageAuthorityVersion": AUTHORITY_VERSION,
                "stageFingerprint": stage.get("stage_fingerprint"),
                "frozenFeatureVectorFingerprint": existing_fingerprint,
                "version": VERSION,
            }

    module._store_prediction = store_prediction
    module.IMMUTABLE_LOCKED_STORAGE_VERSION = VERSION
    module.MLB_CANONICAL_READ_OVERLAY_IDEMPOTENCY_VERSION = (
        CANONICAL_READ_OVERLAY_IDEMPOTENCY_VERSION
    )
    module._INQSI_MLB_IMMUTABLE_LOCKED_STORAGE_APPLIED = True
    return module
