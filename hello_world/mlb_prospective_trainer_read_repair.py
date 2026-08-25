from __future__ import annotations

import copy
import functools
from typing import Any, Dict, List, Optional, Tuple


VERSION = "MLB-PROSPECTIVE-TRAINER-READ-REPAIR-v4-canonical-exact-vector-authority"
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


def _canonical_authority_exact_vector_proven(
    authority: Dict[str, Any],
) -> bool:
    """Accept the canonical reader's independently re-derived exact-vector proof.

    Older immutable lock payloads can omit the legacy ``exactVectorVerified``
    boolean even though the canonical authority has revalidated the vector and
    its selection/training status from the consistently read write-once row.
    Both authority booleans and both error lists must agree before this proof is
    accepted. Any missing flag or error remains fail closed.
    """

    if not isinstance(authority, dict) or not authority:
        return False
    if not all(authority.get(flag) is True for flag in _AUTHORITY_INTEGRITY_FLAGS):
        return False
    return not _strings(authority.get("exactLockVectorValidationErrors")) and not _strings(
        authority.get("selectionLockVectorStatusValidationErrors")
    )


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
        copy.deepcopy(out.get("canonicalLockAuthority") or {})
        if isinstance(out.get("canonicalLockAuthority"), dict)
        else {}
    )
    exact_errors = _strings(
        out.get("exactVectorValidationErrors")
        or freeze.get("exactVectorValidationErrors")
    )
    immutable_lock = bool(
        out.get("immutablePerGameStage") is True
        or out.get("immutableLockedStorage") is True
    )
    legacy_exact_vector_proven = bool(
        out.get("exactVectorVerified") is True
        and freeze.get("exactVectorVerified", True) is not False
        and not exact_errors
    )
    canonical_exact_vector_proven = _canonical_authority_exact_vector_proven(
        authority
    )
    exact_vector_proven = bool(
        legacy_exact_vector_proven or canonical_exact_vector_proven
    )
    vector = out.get("frozenFeatureVector")
    verified_lock = bool(
        out.get("lockedPrediction") is True
        and immutable_lock
        and exact_vector_proven
        and not exact_errors
        and isinstance(vector, dict)
        and bool(vector.get("fingerprint"))
    )
    if not verified_lock:
        return out

    row_reasons = _strings(out.get("trainingExclusionReasons"))
    freeze_reasons = _strings(freeze.get("trainingExclusionReasons"))
    all_reasons = row_reasons | freeze_reasons
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
    if not cleared and not stale_false_boolean:
        return out

    exact_proof_source = (
        "canonical_lock_authority"
        if canonical_exact_vector_proven
        else "legacy_locked_payload"
    )
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
        authority_reasons = _strings(authority.get("trainingExclusionReasons"))
        authority_cleared = sorted(
            authority_reasons & EXPIRED_PRELOCK_ONLY_TRAINING_EXCLUSIONS
        )
        authority_remaining = sorted(
            authority_reasons - EXPIRED_PRELOCK_ONLY_TRAINING_EXCLUSIONS
        )
        authority_integrity = all(
            authority.get(flag) is True for flag in _AUTHORITY_INTEGRITY_FLAGS
        )
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
            data = copied.get("data")
            if isinstance(data, dict):
                copied["data"] = _copy_with_stale_prelock_exclusions_cleared(
                    data
                )
            authority = original_authority(copied, slate_date)
            if isinstance(authority, dict):
                authority = copy.deepcopy(authority)
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
