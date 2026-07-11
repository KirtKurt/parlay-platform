from __future__ import annotations

from typing import Any, Dict, List, Tuple

VERSION = "MLB-ML-CLEAN-COHORT-HARDENING-v1-coverage-and-freeze-required"


def apply(cohort_module: Any):
    if getattr(cohort_module, "_INQSI_MLB_CLEAN_COHORT_HARDENING_APPLIED", False):
        return cohort_module
    original_eligibility = cohort_module.eligibility
    original_build = cohort_module.build

    def eligibility(row: Dict[str, Any]) -> Tuple[bool, List[str]]:
        ok, reasons = original_eligibility(row)
        reasons = list(reasons or [])
        freeze = row.get("mlFeatureFreeze") or {}
        coverage = row.get("slateCoverage") or {}
        coverage_complete = bool(
            freeze.get("completeSlateCoverage") is True
            or coverage.get("coverageComplete") is True
        )
        if not coverage_complete:
            reasons.append("incomplete_or_unproven_slate_coverage")
        if freeze:
            if freeze.get("trainingEligible") is not True:
                reasons.extend(str(value) for value in (freeze.get("trainingExclusionReasons") or []))
        elif not row.get("frozenFeatureVector"):
            reasons.append("missing_immutable_frozen_feature_vector")
        if row.get("featureVectorFrozenAtLock") is False:
            reasons.append("feature_vector_not_frozen_at_lock")
        reasons = sorted(set(reasons))
        return not reasons, reasons

    def build(rows):
        out = original_build(rows)
        out["hardeningVersion"] = VERSION
        out["completeSlateCoverageRequired"] = True
        out["immutableFrozenFeatureVectorRequired"] = True
        out["version"] = str(out.get("version") or "") + "+" + VERSION
        return out

    cohort_module.eligibility = eligibility
    cohort_module.build = build
    cohort_module._INQSI_MLB_CLEAN_COHORT_HARDENING_APPLIED = True
    return cohort_module
