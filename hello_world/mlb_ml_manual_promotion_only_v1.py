from __future__ import annotations

from typing import Any, Dict

VERSION = "MLB-ML-MANUAL-PROMOTION-ONLY-v1"


def apply(champion_module: Any):
    """Permanently disable automatic champion promotion.

    Direction and playability authority may only be granted by the reviewed
    DynamoDB promotion command after their independent gates pass.
    """
    if getattr(champion_module, "_INQSI_MLB_MANUAL_PROMOTION_ONLY_APPLIED", False):
        return champion_module

    def no_automatic_promotion(bundle: Dict[str, Any]) -> Dict[str, Any]:
        gate = (bundle or {}).get("promotionGate") or {}
        return {
            "ok": True,
            "promoted": False,
            "reason": "automatic_promotion_permanently_disabled_manual_review_required",
            "directionPromotionEligible": bool(gate.get("directionPromotionEligible")),
            "playabilityPromotionEligible": bool(gate.get("playabilityPromotionEligible")),
            "manualPromotionFunction": "promote_reviewed_latest",
            "manualPromotionWorkflow": "mlb-ml-promote-champion.yml",
            "version": VERSION,
        }

    champion_module.promote_if_allowed = no_automatic_promotion
    champion_module.AUTO_PROMOTE = False
    champion_module.AUTOMATIC_PROMOTION_SUPPORTED = False
    champion_module.AUTOMATIC_PROMOTION_PERMANENTLY_DISABLED = True
    champion_module.MANUAL_PROMOTION_ONLY_VERSION = VERSION
    champion_module._INQSI_MLB_MANUAL_PROMOTION_ONLY_APPLIED = True
    return champion_module
