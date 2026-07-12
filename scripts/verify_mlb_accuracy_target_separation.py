#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_accuracy_target_policy_v1 as policy


def _dual_model(selected_accuracy: float):
    return {
        "ok": True,
        "status": "TRAINED",
        "testWasUntouchedDuringFitAndThresholdSelection": True,
        "split": {"counts": {"train": 300, "validation": 100, "test": 100}},
        "dataQuality": {
            "modelScope": "MARKET_MOVEMENT_ONLY_WITH_MISSINGNESS",
            "averageFundamentalsCompletenessPct": 0.0,
        },
        "untouchedTest": {
            "outcome": {
                "count": 100,
                "accuracyLiftPctPoints": 2.0,
                "brierSkillPct": 1.0,
                "logLoss": 0.60,
                "calibrationError": 0.05,
                "baseline": {"logLoss": 0.70},
            },
            "selectedReliability": {
                "count": 50,
                "accuracyPct": selected_accuracy,
                "priceCoveragePct": 100.0,
                "flatUnitRoiPct": 1.0,
                "calibrationError": 0.05,
            },
        },
    }


def main() -> int:
    installed = policy.install()
    assert installed.get("ok") is True, installed
    assert installed.get("rolling24hAllGamesAuditTargetPct") == 90.0
    assert installed.get("recommendationReliabilityThresholdPct") == 60.0

    assert os.environ.get("INQSI_MLB_ML_TARGET_ACCURACY") == "60.0"
    assert os.environ.get("INQSI_MLB_ML_PLAYABLE_TARGET_ACCURACY") == "60.0"
    assert os.environ.get("INQSI_MLB_ML_MIN_SELECTED_RELIABILITY_ACCURACY") == "60.0"
    assert os.environ.get("INQSI_MLB_ROLLING_24H_ALL_GAMES_TARGET_ACCURACY") == "90.0"

    import mlb_ml_runtime_safety_patch as runtime_safety
    import mlb_ml_champion_challenger_v1 as champion
    import mlb_real_world_accuracy_semantics_fix as semantics

    assert runtime_safety.MIN_ACCURACY_TARGET_PCT == 60.0
    assert champion.MIN_SELECTED_RELIABILITY_ACCURACY == 60.0
    assert semantics.ROLLING_24H_ALL_GAMES_AUDIT_TARGET_PCT == 90.0
    assert semantics.MIN_PLAYABLE_TARGET_ACCURACY_PCT == 60.0

    at_threshold = champion.evaluate(_dual_model(60.0), clean_count=500, playable_evidence_count=200)
    assert at_threshold.get("playabilityPromotionEligible") is True, at_threshold
    assert at_threshold.get("recommendationReliabilityThresholdPct") == 60.0
    assert at_threshold.get("rolling24hAllGamesAuditTargetPct") == 90.0

    below_threshold = champion.evaluate(_dual_model(59.99), clean_count=500, playable_evidence_count=200)
    assert below_threshold.get("playabilityPromotionEligible") is False, below_threshold
    codes = {item.get("code") for item in below_threshold.get("playabilityBlockers") or []}
    assert "SELECTED_ACCURACY_TOO_LOW" in codes, below_threshold

    rolling_source = (HELLO_WORLD / "mlb_rolling_24h_audit.py").read_text(encoding="utf-8")
    assert "TARGET_ACCURACY_PCT = 90.0" in rolling_source

    semantics_source = (HELLO_WORLD / "mlb_real_world_accuracy_semantics_fix.py").read_text(encoding="utf-8")
    assert '"targetAccuracyPct": all_games_audit_target' in semantics_source
    assert '"playableRecommendationAccuracyThresholdPct": playable_threshold' in semantics_source
    assert "auditTargetDoesNotSuppressRecommendations" in semantics_source

    print(
        "MLB accuracy targets verified: 90% applies only to the rolling 24-hour all-games audit; "
        "60% applies to recommendation/reliability validation."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
