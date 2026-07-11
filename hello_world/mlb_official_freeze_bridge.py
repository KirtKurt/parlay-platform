from __future__ import annotations

from typing import Any, Dict

VERSION = "MLB-OFFICIAL-FREEZE-BRIDGE-v2-exact-clean-cohort-vector"


def apply(semantics_module: Any):
    if getattr(semantics_module, "_INQSI_MLB_OFFICIAL_FREEZE_BRIDGE_APPLIED_V2", False):
        return semantics_module
    original_enhance = semantics_module.enhance_result

    def enhanced(result: Dict[str, Any]) -> Dict[str, Any]:
        out = original_enhance(result)
        try:
            import mlb_ml_frozen_features
            import mlb_ml_exact_lock_vector_patch

            mlb_ml_exact_lock_vector_patch.apply(mlb_ml_frozen_features)
            frozen = mlb_ml_frozen_features.enhance_result(out)
            if isinstance(frozen, dict):
                status = dict(frozen.get("mlFeatureFreeze") or {})
                status["officialFreezeBridgeVersion"] = VERSION
                status["exactCleanCohortVectorRequired"] = True
                frozen["mlFeatureFreeze"] = status
            return frozen
        except Exception as exc:
            if isinstance(out, dict):
                out["mlFeatureFreeze"] = {
                    "applied": False,
                    "error": str(exc),
                    "bridgeVersion": VERSION,
                    "exactCleanCohortVectorRequired": True,
                }
            return out

    semantics_module.enhance_result = enhanced
    semantics_module._INQSI_MLB_OFFICIAL_FREEZE_BRIDGE_APPLIED = True
    semantics_module._INQSI_MLB_OFFICIAL_FREEZE_BRIDGE_APPLIED_V2 = True
    return semantics_module
