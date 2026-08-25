from __future__ import annotations

import copy
import functools
from typing import Any, Dict, List, Optional, Tuple


VERSION = "MLB-PROSPECTIVE-TRAINER-READ-REPAIR-v4-revalidated-exact-proof"
_INSTALL_FLAG = "_INQSI_MLB_PROSPECTIVE_TRAINER_READ_REPAIR_V4"
_AUTHORITY_FLAG = "_INQSI_MLB_PROSPECTIVE_AUTHORITY_READ_REPAIR_V4"
_VERDICT_FLAG = "_INQSI_MLB_PROSPECTIVE_VERDICT_READ_REPAIR_V4"
_JOIN_FLAG = "_INQSI_MLB_PROSPECTIVE_JOIN_READ_REPAIR_V4"
EXPIRED_PRELOCK_ONLY_TRAINING_EXCLUSIONS = frozenset(
    {
        "immutable_tminus45_prediction_not_available",
        "incomplete_slate_coverage",
    }
)
_AUTHORITY_INTEGRITY_FLAGS = (
    "verified",
    "consistentRead",
    "immutableLocked",
    "stageAuthorityVerified",
    "persistedStageAuthorityValidated",
    "officialAuditEligible",
    "exactLockVectorValidated",
    "selectionLockVectorStatusValidated",
)


def _strings(values: Any) -> set[str]:
    return {str(value) for value in (values or []) if str(value)}


def _authority_integrity_proven(authority: Any) -> bool:
    return bool(
        isinstance(authority, dict)
        and authority
        and all(authority.get(flag) is True for flag in _AUTHORITY_INTEGRITY_FLAGS)
    )


def _exact_vector_proof(
    row: Dict[str, Any],
    freeze: Dict[str, Any],
    authority: Dict[str, Any],
) -> Tuple[bool, List[str], Optional[str]]:
    """Resolve exact-vector proof without treating a legacy missing alias as failure.

    New rows normally carry the same verified flag at both the top level and in
    ``mlFeatureFreeze``. Older immutable rows can carry only one of those
    aliases. An explicitly contradictory stored boolean remains fail-closed.
    Once canonical authority has revalidated the exact vector and its status,
    that independently derived authority may also prove the read-only repair.
    """

    exact_errors = sorted(
        {
            *_strings(row.get("exactVectorValidationErrors")),
            *_strings(freeze.get("exactVectorValidationErrors")),
            *_strings(authority.get("exactLockVectorValidationErrors")),
            *_strings(authority.get("selectionLockVectorStatusValidationErrors")),
        }
    )
    stored_flags = [
        value
        for present, value in (
            ("exactVectorVerified" in row, row.get("exactVectorVerified")),
            (
                "exactVectorVerified" in freeze,
                freeze.get("exactVectorVerified"),
            ),
        )
        if present
    ]
    stored_metadata_proven = bool(
        stored_flags and all(value is True for value in stored_flags)
    )
    authority_proven = _authority_integrity_proven(authority)
    proven = bool(
        not exact_errors and (stored_metadata_proven or authority_proven)
    )
    source = (
        "CANONICAL_LOCK_AUTHORITY_REVALIDATION"
        if authority_proven and not stored_metadata_proven
        else "STORED_EXACT_VECTOR_METADATA"
        if stored_metadata_proven
        else None
    )
    return proven, exact_errors, source


def _copy_with_stale_prelock_exclusions_cleared(
    row: Dict[str, Any],
) -> Dict[str, Any]:
    """Return a read-only corrected copy of one verified immutable lock.

    The stale exclusions are valid while a prediction is merely pre-lock. They
    become false only after the same selection has a verified immutable T-45
    lock and exact vector. A legacy false eligibility boolean with no remaining
    exclusion is also stale once every immutable-vector proof is present.
    Every substantive exclusion remains authoritative.
    """

    out = copy.deepcopy(row or {})
    freeze = (
        dict(out.get("mlFeatureFreeze") or {})
        if isinstance(out.get("mlFeatureFreeze"), dict)
        else {}
    )
    authority = (
        dict(out.get("canonicalLockAuthority") or {})
        if isinstance(out.get("canonicalLockAuthority"), dict)
        else {}
    )
    exact_proven, exact_errors, exact_proof_source = _exact_vector_proof(
        out,
        freeze,
        authority,
    )
    immutable_lock = bool(
        out.get("immutablePerGameStage") is True
        or out.get("immutableLockedStorage") is True
    )
    vector = out.get("frozenFeatureVector")
    verified_lock = bool(
        out.get("lockedPrediction") is True
        and immutable_lock
        and exact_proven
        and not exact_errors
        and isinstance(vector, dict)
        and bool(vector.get("fingerprint"))
    )
    if not verified_lock:
        return out

    row_reasons = _strings(out.get("trainingExclusionReasons"))
    freeze_reasons = _strings(freeze.get("trainingExclusionReasons"))
    authority_reasons = _strings(authority.get("trainingExclusionReasons"))
    all_reasons = row_reasons | freeze_reasons | authority_reasons
    cleared = sorted(all_reasons & EXPIRED_PRELOCK_ONLY_TRAINING_EXCLUSIONS)
    remaining = sorted(all_reasons - EXPIRED_PRELOCK_ONLY_TRAINING_EXCLUSIONS)

    # Do not infer eligibility through any surviving exclusion. The only
    # no-reason correction allowed here is the legacy false boolean on a fully
    # verified immutable lock; the immutable payload itself is never written.
    eligible = not remaining
    stale_false_boolean = bool(
        eligible
        and (
            out.get("trainingEligible") is not True
            or freeze.get("trainingEligible") is not True
        )
    )
    authority_stale_false = bool(
        eligible
        and _authority_integrity_proven(authority)
        and authority.get("learningEligible") is not True
    )
    if not cleared and not stale_false_boolean and not authority_stale_false:
        return out

    metadata = {
        "trainingEligible": eligible,
        "trainingExclusionReasons": remaining,
        "expiredPrelockTrainingExclusionsClearedAtRead": cleared,
        "staleTrainingEligibleBooleanClearedAtRead": stale_false_boolean,
        "exactVectorProofSourceAtRead": exact_proof_source,
        "prospectiveTrainerReadRepairVersion": VERSION,
    }
    freeze.update(metadata)
    out.update(
        {
            **metadata,
            "trainingEligibilityStatus": (
                "ELIGIBLE" if eligible else "INELIGIBLE"
            ),
            "mlFeatureFreeze": freeze,
        }
    )

    if authority:
        authority_cleared = sorted(
            authority_reasons & EXPIRED_PRELOCK_ONLY_TRAINING_EXCLUSIONS
        )
        authority_remaining = sorted(
            authority_reasons - EXPIRED_PRELOCK_ONLY_TRAINING_EXCLUSIONS
        )
        authority_integrity = _authority_integrity_proven(authority)
        authority_stale_false = bool(
            eligible
            and authority_integrity
            and not authority_remaining
            and authority.get("learningEligible") is not True
        )
        authority.update(
            {
                "trainingExclusionReasons": authority_remaining,
                "expiredPrelockTrainingExclusionsClearedAtRead": (
                    authority_cleared
                ),
                "staleLearningEligibleBooleanClearedAtRead": (
                    authority_stale_false
                ),
                "learningEligible": bool(
                    eligible and authority_integrity and not authority_remaining
                ),
                "exactVectorProofSourceAtRead": exact_proof_source,
                "prospectiveTrainerReadRepairVersion": VERSION,
            }
        )
        out["canonicalLockAuthority"] = authority
    return out


def _label_with_stale_prelock_state_cleared(
    label: Dict[str, Any],
    *,
    current_lock_eligible: bool,
    current_lock_exclusions: List[str],
) -> Dict[str, Any]:
    """Correct only proven-stale pre-lock label state in memory.

    A historical false label boolean may be cleared only when the current
    immutable lock is independently eligible, has no current exclusions, and
    the persisted label has no substantive exclusion remaining. Stored label
    evidence is never mutated.
    """

    out = copy.deepcopy(label or {})
    label_reasons = _strings(out.get("training_exclusion_reasons"))
    cleared = sorted(label_reasons & EXPIRED_PRELOCK_ONLY_TRAINING_EXCLUSIONS)
    remaining = sorted(label_reasons - EXPIRED_PRELOCK_ONLY_TRAINING_EXCLUSIONS)
    stale_false_boolean = bool(
        out.get("training_eligible") is not True
        and current_lock_eligible
        and not current_lock_exclusions
        and not remaining
    )
    if not cleared and not stale_false_boolean:
        return out
    eligible = bool(
        current_lock_eligible
        and not current_lock_exclusions
        and not remaining
    )
    out.update(
        {
            "training_eligible": eligible,
            "training_exclusion_reasons": remaining,
            "expired_prelock_training_exclusions_cleared_at_read": cleared,
            "stale_training_eligible_boolean_cleared_at_read": stale_false_boolean,
            "prospective_trainer_read_repair_version": VERSION,
        }
    )
    return out


def install(labels: Optional[Any] = None) -> Any:
    """Install a fail-closed, in-memory repair on the trainer's label reader."""

    if labels is None:
        import mlb_canonical_final_labels_v1 as labels_module

        labels = labels_module
    if getattr(labels, _INSTALL_FLAG, False):
        return labels

    rolling = getattr(labels, "rolling_audit", None)
    original_authority = getattr(rolling, "_canonical_lock_authority", None)
    if callable(original_authority) and not getattr(
        original_authority, _AUTHORITY_FLAG, False
    ):

        @functools.wraps(original_authority)
        def canonical_lock_authority(
            item: Dict[str, Any], slate_date: str
        ) -> Dict[str, Any]:
            copied = copy.deepcopy(item or {})
            proof_source_at_read: Optional[str] = None
            data = copied.get("data")
            if isinstance(data, dict):
                normalized_data = _copy_with_stale_prelock_exclusions_cleared(
                    data
                )
                proof_source_at_read = normalized_data.get(
                    "exactVectorProofSourceAtRead"
                )
                copied["data"] = normalized_data
            authority = original_authority(copied, slate_date)
            if isinstance(authority, dict):
                authority = copy.deepcopy(authority)
                # The canonical authority revalidates the exact vector from the
                # immutable row. Feed that proof through the same read-only
                # compatibility boundary so missing legacy aliases cannot leave
                # a proven row permanently false.
                normalized_data = copied.get("data")
                if isinstance(normalized_data, dict):
                    with_authority = copy.deepcopy(normalized_data)
                    with_authority["canonicalLockAuthority"] = authority
                    normalized = _copy_with_stale_prelock_exclusions_cleared(
                        with_authority
                    )
                    normalized_authority = normalized.get(
                        "canonicalLockAuthority"
                    )
                    if isinstance(normalized_authority, dict):
                        authority = normalized_authority
                        proof_source_at_read = (
                            normalized_authority.get(
                                "exactVectorProofSourceAtRead"
                            )
                            or proof_source_at_read
                        )
                if proof_source_at_read:
                    authority["exactVectorProofSourceAtRead"] = (
                        proof_source_at_read
                    )
                authority["prospectiveTrainerReadRepairVersion"] = VERSION
                authority["immutableLockPayloadMutated"] = False
            return authority

        setattr(canonical_lock_authority, _AUTHORITY_FLAG, True)
        rolling._canonical_lock_authority = canonical_lock_authority

    original_verdict = getattr(labels, "_training_verdict", None)
    if callable(original_verdict) and not getattr(
        original_verdict, _VERDICT_FLAG, False
    ):

        @functools.wraps(original_verdict)
        def training_verdict(row: Dict[str, Any]) -> Tuple[bool, List[str]]:
            return original_verdict(
                _copy_with_stale_prelock_exclusions_cleared(row)
            )

        setattr(training_verdict, _VERDICT_FLAG, True)
        labels._training_verdict = training_verdict

    original_join = getattr(labels, "_joined_training_row", None)
    if callable(original_join) and not getattr(original_join, _JOIN_FLAG, False):

        @functools.wraps(original_join)
        def joined_training_row(
            slate_date: str,
            label: Dict[str, Any],
            locked: Dict[str, Any],
            *,
            slate_finalized: bool,
        ) -> Dict[str, Any]:
            clean_locked = _copy_with_stale_prelock_exclusions_cleared(locked)
            current_eligible, current_exclusions = labels._training_verdict(
                clean_locked
            )
            clean_label = _label_with_stale_prelock_state_cleared(
                label,
                current_lock_eligible=current_eligible,
                current_lock_exclusions=current_exclusions,
            )
            joined = original_join(
                slate_date,
                clean_label,
                clean_locked,
                slate_finalized=slate_finalized,
            )
            if isinstance(joined, dict):
                joined = copy.deepcopy(joined)
                joined.update(
                    {
                        "prospectiveTrainerReadRepairVersion": VERSION,
                        "immutablePregameVectorMutated": False,
                        "immutableLockPayloadMutated": False,
                        "immutableLabelPayloadMutated": False,
                    }
                )
            return joined

        setattr(joined_training_row, _JOIN_FLAG, True)
        labels._joined_training_row = joined_training_row

    labels.MLB_PROSPECTIVE_TRAINER_READ_REPAIR_VERSION = VERSION
    setattr(labels, _INSTALL_FLAG, True)
    return labels
