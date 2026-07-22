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
    vector = row.get("frozenFeatureVector") or {}
    return {
        "version": vector.get("version"),
        "fingerprint": vector.get("fingerprint"),
        "gameId": vector.get("gameId"),
        "lockAtUtc": vector.get("lockAtUtc"),
        "sourcePullAtUtc": vector.get("sourcePullAtUtc"),
        "predictedWinner": vector.get("predictedWinner"),
        "predictedSide": vector.get("predictedSide"),
        "labels": vector.get("labels") or {},
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
    module._INQSI_MLB_IMMUTABLE_LOCKED_STORAGE_APPLIED = True
    return module
