from __future__ import annotations

from typing import Any

VERSION = "MLB-DIRECTIONAL-OUTCOME-BRIDGE-v1"


def apply(directional_module: Any):
    if getattr(directional_module, "_INQSI_MLB_DIRECTIONAL_OUTCOME_BRIDGE_APPLIED", False):
        return directional_module
    original_apply = directional_module.apply

    def bridged_apply(engine_module: Any):
        engine_module = original_apply(engine_module)
        try:
            import mlb_ml_outcome_runtime
            mlb_ml_outcome_runtime.apply(engine_module)
        except Exception:
            pass
        return engine_module

    directional_module.apply = bridged_apply
    directional_module._INQSI_MLB_DIRECTIONAL_OUTCOME_BRIDGE_APPLIED = True
    return directional_module
