from __future__ import annotations

from typing import Any, Dict, Optional

VERSION = "MLB-ML-EXACT-LOCK-VECTOR-PATCH-v2-temporal-missingness-contract"


def _count(value: Any) -> Optional[int]:
    try:
        if value in (None, ""):
            return None
        parsed = int(value)
        return parsed if parsed >= 0 else None
    except Exception:
        return None


def _pull_slot_contract_errors(snapshot: Dict[str, Any]) -> list[str]:
    integrity = snapshot.get("pullHistoryIntegrity") or {}
    source_slot = snapshot.get("predictionSourceCanonicalSlot") or {}
    errors: list[str] = []
    if not isinstance(integrity, dict) or not integrity:
        return ["pull_history_integrity_missing"]

    unique_count = _count(integrity.get("uniqueSlotCount"))
    raw_count = _count(integrity.get("rawPullCount"))
    duplicate_count = _count(integrity.get("duplicatePullCount"))
    invalid_count = _count(integrity.get("invalidPullCount"))
    contaminated_count = _count(integrity.get("contaminatedSlotCount"))
    if not integrity.get("version"):
        errors.append("pull_history_integrity_version_missing")
    if not integrity.get("canonicalizationVersion"):
        errors.append("pull_slot_canonicalization_version_missing")
    if unique_count is None or unique_count <= 0:
        errors.append("canonical_pull_slot_count_missing")
    if raw_count is None or unique_count is None or raw_count != unique_count:
        errors.append("raw_and_unique_pull_slot_counts_mismatch")
    if duplicate_count is None or duplicate_count != 0:
        errors.append("duplicate_pull_observation_count_nonzero")
    if invalid_count is None or invalid_count != 0:
        errors.append("invalid_pull_observation_count_nonzero")
    if contaminated_count is None or contaminated_count != 0:
        errors.append("contaminated_pull_slot_count_nonzero")
    if integrity.get("duplicateContaminated") is not False:
        errors.append("duplicate_pull_slot_contamination")
    if not integrity.get("canonicalSlotFingerprint"):
        errors.append("canonical_pull_slot_fingerprint_missing")

    if not isinstance(source_slot, dict) or not source_slot:
        errors.append("prediction_source_canonical_slot_missing")
        return sorted(set(errors))
    if not source_slot.get("version"):
        errors.append("prediction_source_canonical_slot_version_missing")
    if source_slot.get("canonical") is not True:
        errors.append("prediction_source_slot_not_canonical")
    if not source_slot.get("slotStartUtc"):
        errors.append("prediction_source_slot_start_missing")
    if not source_slot.get("canonicalPullId"):
        errors.append("prediction_source_canonical_pull_id_missing")
    if not source_slot.get("canonicalPulledAtUtc"):
        errors.append("prediction_source_canonical_pull_time_missing")
    if not source_slot.get("canonicalPullFingerprint"):
        errors.append("prediction_source_canonical_pull_fingerprint_missing")
    if source_slot.get("contaminated") is not False:
        errors.append("prediction_source_canonical_slot_contaminated")
    if _count(source_slot.get("duplicatePullCount")) != 0:
        errors.append("prediction_source_duplicate_pull_count_nonzero")
    if _count(source_slot.get("invalidPullCount")) != 0:
        errors.append("prediction_source_invalid_pull_count_nonzero")
    slot_starts = integrity.get("slotStartsUtc") or []
    if (
        source_slot.get("slotStartUtc")
        and (
            not isinstance(slot_starts, list)
            or str(source_slot.get("slotStartUtc"))
            not in {str(value) for value in slot_starts}
        )
    ):
        errors.append("prediction_source_slot_not_in_pull_history")
    return sorted(set(errors))


def apply(frozen_module: Any):
    """Add the exact clean-cohort vector to every pregame locked prediction row.

    The existing frozen-feature module captures operational feature groups. The
    clean trainer consumes a separate canonical `frozenFeatureVector` contract.
    This patch creates that contract before storage and deliberately leaves the
    outcome labels empty. Final labels are joined only after settlement.
    """
    if getattr(frozen_module, "_INQSI_MLB_EXACT_LOCK_VECTOR_PATCH_APPLIED", False):
        return frozen_module

    original_freeze_row = frozen_module.freeze_row

    def freeze_row(row: Dict[str, Any], coverage_complete: Optional[bool] = None) -> Dict[str, Any]:
        out = original_freeze_row(row, coverage_complete=coverage_complete)
        freeze = dict(out.get("mlFeatureFreeze") or {})
        try:
            import mlb_ml_clean_cohort_v1 as cohort

            snapshot = cohort.freeze_feature_snapshot(out)
            # Outcomes are unavailable at lock. Keeping these null prevents
            # accidental target leakage while preserving the immutable features.
            snapshot["labels"] = {"homeWon": None, "pickCorrect": None}
            out["frozenFeatureVector"] = snapshot
            out["frozenFeatureVectorVersion"] = snapshot.get("version")
            out["featureVectorFrozenAtLock"] = True
            slot_errors = _pull_slot_contract_errors(snapshot)

            exact_ok = bool(
                snapshot.get("fingerprint")
                and snapshot.get("gameId")
                and snapshot.get("lockAtUtc")
                and snapshot.get("sourcePullAtUtc")
                and isinstance(snapshot.get("features"), dict)
                and snapshot.get("features")
                and snapshot.get("temporalFeaturesAtOrBeforeLock") is True
                and snapshot.get("temporalSourcePullAtUtc")
                and snapshot.get("fundamentalMasksAtOrBeforeLock") is True
                and snapshot.get("fundamentalsSnapshotAsOfUtc")
                and not slot_errors
            )
            freeze.update({
                "exactVectorApplied": True,
                "exactVectorPatchVersion": VERSION,
                "exactVectorCreated": exact_ok,
                "exactVectorVersion": snapshot.get("version"),
                "exactVectorFingerprintVersion": snapshot.get("fingerprintVersion"),
                "exactVectorFingerprint": snapshot.get("fingerprint"),
                "outcomeLabelsJoinedOnlyAfterFinal": True,
            })
            if not exact_ok:
                freeze["trainingEligible"] = False
                reasons = list(freeze.get("trainingExclusionReasons") or [])
                reasons.extend(["exact_lock_vector_incomplete", *slot_errors])
                freeze["trainingExclusionReasons"] = sorted(set(reasons))
                out["trainingEligible"] = False
                out["trainingEligibilityStatus"] = "INELIGIBLE"
                out["trainingExclusionReasons"] = sorted(set(
                    list(out.get("trainingExclusionReasons") or [])
                    + ["exact_lock_vector_incomplete", *slot_errors]
                ))
            freeze["pullHistoryIntegrity"] = snapshot.get("pullHistoryIntegrity") or {}
            freeze["predictionSourceCanonicalSlot"] = snapshot.get("predictionSourceCanonicalSlot") or {}
            freeze["canonicalPullSlotsVerified"] = not slot_errors
        except Exception as exc:
            freeze.update({
                "exactVectorApplied": False,
                "exactVectorPatchVersion": VERSION,
                "exactVectorCreated": False,
                "exactVectorError": str(exc),
                "trainingEligible": False,
            })
            reasons = list(freeze.get("trainingExclusionReasons") or [])
            reasons.append("exact_lock_vector_creation_failed")
            freeze["trainingExclusionReasons"] = sorted(set(reasons))

        integrity = out.get("pullHistoryIntegrity") or {}
        integrity_contaminated = bool(
            isinstance(integrity, dict)
            and (
                integrity.get("duplicateContaminated") is True
                or int(integrity.get("duplicatePullCount") or 0) > 0
                or int(integrity.get("invalidPullCount") or 0) > 0
            )
        )
        probability_corrected = out.get("probabilityCorrectionApplied") is True
        if integrity_contaminated or probability_corrected:
            reasons = {
                str(reason)
                for reason in (freeze.get("trainingExclusionReasons") or [])
                if reason
            }
            if integrity_contaminated:
                reasons.add("duplicate_pull_slot_contamination")
            if probability_corrected:
                reasons.add("probability_direction_integrity_correction")
            freeze["trainingEligible"] = False
            freeze["trainingExclusionReasons"] = sorted(reasons)
            out["trainingEligible"] = False
            out["trainingEligibilityStatus"] = "INELIGIBLE"
            out["trainingExclusionReasons"] = sorted(reasons)
        freeze["pullHistoryIntegrity"] = integrity if isinstance(integrity, dict) else {}
        freeze["canonicalPullSlotsVerified"] = bool(
            isinstance(integrity, dict)
            and integrity.get("canonicalizationVersion")
            and int(integrity.get("uniqueSlotCount") or 0) > 0
        )

        out["mlFeatureFreeze"] = freeze
        return out

    frozen_module.freeze_row = freeze_row
    frozen_module.EXACT_LOCK_VECTOR_PATCH_VERSION = VERSION
    frozen_module._INQSI_MLB_EXACT_LOCK_VECTOR_PATCH_APPLIED = True
    return frozen_module
