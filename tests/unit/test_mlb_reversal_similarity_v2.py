from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELLO = ROOT / "hello_world"
if str(HELLO) not in sys.path:
    sys.path.insert(0, str(HELLO))

import mlb_reversal_similarity_v2 as similarity


def _summary(**full_overrides):
    full = {
        "pullCount": 12,
        "durationMinutes": 180.0,
        "coverageRatio": 1.0,
        "reversalCount": 2,
        "grossMovePp": 6.0,
        "netMovePp": 1.5,
        "pathEfficiency": 0.45,
        "latestLegSignedMovePp": 0.8,
        "latestLegMovePp": 0.8,
        "latestLegDurationMinutes": 45.0,
        "priorLegMovePp": 0.5,
        "reversalRecoveryRatio": 1.6,
        "minutesSinceLastReversal": 45.0,
        "marketFlipCount": 1,
        "largestMarketFlipTowardSidePp": 2.5,
        "largestMarketFlipAgainstSidePp": 0.0,
        "largestMarketFlipAgeMinutes": 75.0,
        "maxReversalSwingPp": 2.5,
        "marketFlip2To3PpCandidate": True,
    }
    full.update(full_overrides)
    return {
        "available": True,
        "horizons": {
            "15m": {"velocityPpHr": 0.2, "reversalCount": 0},
            "60m": {"velocityPpHr": 0.1, "reversalCount": 1},
            "180m": {"velocityPpHr": 0.05, "reversalCount": 2},
            "full": full,
        },
    }


def test_two_to_three_point_market_flip_is_research_only() -> None:
    result = similarity.analyze(_summary())
    assert result["researchCandidate"] is True
    assert result["productionApproved"] is False
    assert result["signalFamily"] == similarity.RESEARCH_SIGNAL_FAMILY
    assert "SIZE_2TO3" in result["similaritySignature"]
    assert "REVERSAL_RESEARCH_CANDIDATE" in result["tags"]


def test_latest_adverse_leg_blocks_reversal_shape() -> None:
    result = similarity.analyze(_summary(latestLegSignedMovePp=-0.8))
    assert result["blocked"] is True
    assert "latest_leg_against_selected_side" in result["riskReasons"]
