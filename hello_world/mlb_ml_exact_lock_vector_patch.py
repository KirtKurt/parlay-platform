from __future__ import annotations

from typing import Any, Dict, Optional

VERSION = "MLB-ML-EXACT-LOCK-VECTOR-PATCH-v1-clean-cohort-contract"


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

            exact_ok = bool(
                snapshot.get("fingerprint")
                and snapshot.get("gameId")
                and snapshot.get("lockAtUtc")
                and snapshot.get("sourcePullAtUtc")
                and isinstance(snapshot.get("features"), dict)
                and snapshot.get("features")
            )
            freeze.update({
                "exactVectorApplied": True,
                "exactVectorPatchVersion": VERSION,
                "exactVectorCreated": exact_ok,
                "exactVectorVersion": snapshot.get("version"),
                "exactVectorFingerprint": snapshot.get("fingerprint"),
                "outcomeLabelsJoinedOnlyAfterFinal": True,
            })
            if not exact_ok:
                freeze["trainingEligible"] = False
                reasons = list(freeze.get("trainingExclusionReasons") or [])
                reasons.append("exact_lock_vector_incomplete")
                freeze["trainingExclusionReasons"] = sorted(set(reasons))
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

        out["mlFeatureFreeze"] = freeze
        return out

    frozen_module.freeze_row = freeze_row
    frozen_module.EXACT_LOCK_VECTOR_PATCH_VERSION = VERSION
    frozen_module._INQSI_MLB_EXACT_LOCK_VECTOR_PATCH_APPLIED = True
    return frozen_module
