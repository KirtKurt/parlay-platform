from __future__ import annotations

import copy
import functools
from typing import Any, Dict, List, Optional, Tuple


VERSION = "MLB-PROSPECTIVE-TRAINER-READ-REPAIR-v1-stale-prelock-state-only"
_INSTALL_FLAG = "_INQSI_MLB_PROSPECTIVE_TRAINER_READ_REPAIR_V1"
_AUTHORITY_FLAG = "_INQSI_MLB_PROSPECTIVE_AUTHORITY_READ_REPAIR_V1"
_VERDICT_FLAG = "_INQSI_MLB_PROSPECTIVE_VERDICT_READ_REPAIR_V1"
_JOIN_FLAG = "_INQSI_MLB_PROSPECTIVE_JOIN_READ_REPAIR_V1"
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


def _copy_with_stale_prelock_exclusions_cleared(
    row: Dict[str, Any],
) -> Dict[str, Any]:
    """Return a read-only corrected copy of one verified immutable lock.

    The stale exclusions are valid while a prediction is merely pre-lock. They
    become false only after the same selection has a verified immutable T-45
    lock and exact vector. Every other exclusion remains authoritative.
    """

    out = copy.deepcopy(row or {})
    freeze = (
        dict(out.get("mlFeatureFreeze") or {})
        if isinstance(out.get("mlFeatureFreeze"), dict)
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
    if not (
        out.get("lockedPrediction") is True
        and immutable_lock
        and out.get("exactVectorVerified") is True
        and not exact_errors
        and isinstance(out.get("frozenFeatureVector"), dict)
        and bool((out.get("frozenFeatureVector") or {}).get("fingerprint"))
    ):
        return out

    row_reasons = _strings(out.get("trainingExclusionReasons"))
    freeze_reasons = _strings(freeze.get("trainingExclusionReasons"))
    all_reasons = row_reasons | freeze_reasons
    cleared = sorted(all_reasons & EXPIRED_PRELOCK_ONLY_TRAINING_EXCLUSIONS)
    if not cleared:
        return out

    remaining = sorted(all_reasons - EXPIRED_PRELOCK_ONLY_TRAINING_EXCLUSIONS)
    eligible = not remaining
    metadata = {
        "trainingEligible": eligible,
        "trainingExclusionReasons": remaining,
        "expiredPrelockTrainingExclusionsClearedAtRead": cleared,
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

    authority = out.get("canonicalLockAuthority")
    if isinstance(authority, dict) and authority:
        authority = copy.deepcopy(authority)
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
        authority.update(
            {
                "trainingExclusionReasons": authority_remaining,
                "expiredPrelockTrainingExclusionsClearedAtRead": (
                    authority_cleared
                ),
                "learningEligible": bool(
                    eligible and authority_integrity and not authority_remaining
                ),
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
    """Correct an immutable label only in memory for a verified current lock."""

    out = copy.deepcopy(label or {})
    label_reasons = _strings(out.get("training_exclusion_reasons"))
    cleared = sorted(label_reasons & EXPIRED_PRELOCK_ONLY_TRAINING_EXCLUSIONS)
    if not cleared:
        return out
    remaining = sorted(label_reasons - EXPIRED_PRELOCK_ONLY_TRAINING_EXCLUSIONS)
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