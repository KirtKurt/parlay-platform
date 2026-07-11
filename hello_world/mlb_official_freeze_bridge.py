from __future__ import annotations

from typing import Any, Dict

VERSION = "MLB-OFFICIAL-FREEZE-BRIDGE-v1"


def apply(semantics_module: Any):
    if getattr(semantics_module, "_INQSI_MLB_OFFICIAL_FREEZE_BRIDGE_APPLIED", False):
        return semantics_module
    original_enhance = semantics_module.enhance_result

    def enhanced(result: Dict[str, Any]) -> Dict[str, Any]:
        out = original_enhance(result)
        try:
            import mlb_ml_frozen_features
            return mlb_ml_frozen_features.enhance_result(out)
        except Exception as exc:
            if isinstance(out, dict):
                out["mlFeatureFreeze"] = {"applied": False, "error": str(exc), "bridgeVersion": VERSION}
            return out

    semantics_module.enhance_result = enhanced
    semantics_module._INQSI_MLB_OFFICIAL_FREEZE_BRIDGE_APPLIED = True
    return semantics_module
