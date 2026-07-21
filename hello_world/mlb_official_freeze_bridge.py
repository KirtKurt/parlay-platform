from __future__ import annotations

from typing import Any, Dict

VERSION = "MLB-OFFICIAL-FREEZE-BRIDGE-v3-explicit-lock-writer-owned"


def apply(semantics_module: Any):
    if getattr(semantics_module, "_INQSI_MLB_OFFICIAL_FREEZE_BRIDGE_APPLIED_V2", False):
        return semantics_module
    original_enhance = semantics_module.enhance_result

    def enhanced(result: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize display/official semantics without creating evidence.

        Exact feature freezing belongs to the explicit T-45 lock writer, which
        has the persisted candidate, canonical pull proof, and scheduled
        evidence boundary.  A generic semantics/read path must never synthesize
        a new vector or change training eligibility.
        """
        out = original_enhance(result)
        if isinstance(out, dict):
            status = dict(out.get("mlFeatureFreeze") or {})
            status.update({
                "officialFreezeBridgeVersion": VERSION,
                "exactCleanCohortVectorRequiredAtTMinus45": True,
                "exactVectorCreationOwner": "explicit_per_game_tminus45_lock_writer",
                "readPathMayCreateOrRewriteVector": False,
            })
            out["mlFeatureFreeze"] = status
        return out

    semantics_module.enhance_result = enhanced
    semantics_module._INQSI_MLB_OFFICIAL_FREEZE_BRIDGE_APPLIED = True
    semantics_module._INQSI_MLB_OFFICIAL_FREEZE_BRIDGE_APPLIED_V2 = True
    return semantics_module
