from __future__ import annotations

from typing import Any, Dict

VERSION = "MLB-ML-PROMOTION-SAFETY-v1-manual-review-only"


def apply(champion_module: Any):
    if getattr(champion_module, "_INQSI_MLB_MANUAL_PROMOTION_ONLY_APPLIED", False):
        return champion_module

    champion_module.AUTO_PROMOTE = False

    def manual_only(bundle: Dict[str, Any]) -> Dict[str, Any]:
        gate = (bundle or {}).get("promotionGate") or {}
        return {
            "ok": True,
            "promoted": False,
            "reason": "automatic_promotion_permanently_disabled_manual_review_required",
            "directionPromotionEligible": bool(gate.get("directionPromotionEligible")),
            "playabilityPromotionEligible": bool(gate.get("playabilityPromotionEligible")),
            "manualPromotionWorkflow": "mlb-ml-promote-champion.yml",
            "version": VERSION,
        }

    champion_module.promote_if_allowed = manual_only
    champion_module.AUTOMATIC_PROMOTION_PERMANENTLY_DISABLED = True
    champion_module._INQSI_MLB_MANUAL_PROMOTION_ONLY_APPLIED = True
    return champion_module
