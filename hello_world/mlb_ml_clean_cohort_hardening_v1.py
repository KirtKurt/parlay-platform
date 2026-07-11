from __future__ import annotations

from typing import Any, Dict, List, Tuple

VERSION = "MLB-ML-CLEAN-COHORT-HARDENING-v2-exact-stored-lock-vector-required"


def apply(cohort_module: Any):
    if getattr(cohort_module, "_INQSI_MLB_CLEAN_COHORT_HARDENING_V2_APPLIED", False):
        return cohort_module
    original_eligibility = cohort_module.eligibility
    original_build = cohort_module.build

    def eligibility(row: Dict[str, Any]) -> Tuple[bool, List[str]]:
        _, reasons = original_eligibility(row)
        reasons = list(reasons or [])
        freeze = row.get("mlFeatureFreeze") or {}
        coverage = row.get("slateCoverage") or {}
        frozen_vector = row.get("frozenFeatureVector") or {}
        coverage_complete = bool(
            freeze.get("completeSlateCoverage") is True
            or coverage.get("coverageComplete") is True
        )
        if not coverage_complete:
            reasons.append("incomplete_or_unproven_slate_coverage")
        if freeze and freeze.get("trainingEligible") is not True:
            reasons.extend(str(value) for value in (freeze.get("trainingExclusionReasons") or []))
        if not isinstance(frozen_vector, dict) or not frozen_vector.get("fingerprint"):
            reasons.append("missing_exact_stored_lock_time_feature_vector")
        if str(frozen_vector.get("version") or "") != str(cohort_module.FEATURE_SNAPSHOT_VERSION):
            reasons.append("wrong_frozen_feature_vector_version")
        if frozen_vector.get("immutableSource") != "locked_prediction_row_pre_game_features":
            reasons.append("feature_vector_source_not_lock_time_prediction")
        if frozen_vector.get("derivedOnceFromImmutableLockedRow") is not True:
            reasons.append("feature_vector_not_proven_immutable")
        vector_source = cohort_module._parse_dt(frozen_vector.get("sourcePullAtUtc"))
        vector_lock = cohort_module._parse_dt(frozen_vector.get("lockAtUtc"))
        if not vector_source or not vector_lock or vector_source > vector_lock:
            reasons.append("frozen_vector_source_after_or_missing_lock")
        reasons = sorted(set(reasons))
        return not reasons, reasons

    def build(rows):
        out = original_build(rows)
        out["hardeningVersion"] = VERSION
        out["completeSlateCoverageRequired"] = True
        out["immutableFrozenFeatureVectorRequired"] = True
        out["laterFeatureReconstructionAllowed"] = False
        out["requiredFeatureVectorVersion"] = cohort_module.FEATURE_SNAPSHOT_VERSION
        out["version"] = str(out.get("version") or "") + "+" + VERSION
        return out

    cohort_module.eligibility = eligibility
    cohort_module.build = build
    cohort_module._INQSI_MLB_CLEAN_COHORT_HARDENING_APPLIED = True
    cohort_module._INQSI_MLB_CLEAN_COHORT_HARDENING_V2_APPLIED = True
    return cohort_module
