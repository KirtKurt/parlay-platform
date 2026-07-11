#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_ml_champion_challenger_v1 as champion
import mlb_ml_manual_promotion_only_v1 as safety


def main() -> int:
    safety.apply(champion)
    assert champion.AUTO_PROMOTE is False
    assert champion.AUTOMATIC_PROMOTION_SUPPORTED is False
    assert champion.MANUAL_PROMOTION_ONLY_VERSION == "MLB-ML-MANUAL-PROMOTION-ONLY-v1"
    result = champion.promote_if_allowed({
        "promotionGate": {
            "promotionDecision": "PROMOTE",
            "directionPromotionEligible": True,
            "playabilityPromotionEligible": True,
        }
    })
    assert result["promoted"] is False
    assert result["reason"] == "automatic_promotion_permanently_disabled_manual_review_required"
    assert callable(champion.promote_reviewed_latest)
    print("MLB ML promotion safety verified: automatic promotion is impossible; reviewed DynamoDB promotion is required")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
