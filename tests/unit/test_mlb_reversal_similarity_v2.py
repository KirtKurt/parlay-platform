from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_reversal_similarity_v2 as similarity


def _selected() -> dict:
    return {
        "reversalCount": 2,
        "temporalFeatures": {
            "signalQualityIndex": 68.0,
            "horizons": {
                "full": {
                    "reversalCount": 2,
                    "pathEfficiency": 0.40,
                    "latestReversalRecoveryRatio": 1.5,
                    "latestReversalMinutesBeforeEvent": 120.0,
                    "reversalMarketFlipCount": 1,
                    "latestLeg": {"amplitudePp": 2.4, "direction": 1},
                    "previousLeg": {"amplitudePp": 1.6, "direction": -1},
                    "latestReversalMarketFlip": {"amplitudePp": 2.4},
                    "market": {
                        "eligibleBookCount": 5,
                        "weightedBookDirectionAgreement": 0.42,
                        "latestBookRangePp": 4.0,
                    },
                    "signalQuality": {"signalQualityIndex": 68.0},
                }
            },
        },
    }


def test_signature_is_deterministic_and_captures_similarity_dimensions() -> None:
    selected = _selected()
    assert similarity.signature(selected) == similarity.signature(selected)
    payload = similarity.signature_payload(selected)
    assert payload["latestLegAmplitudeBandPp"] == "2-3"
    assert payload["reversalTimingBandMinutesBeforeEvent"] == "<=180"
    assert payload["marketFlip"] is True
    assert payload["bookAgreementBand"] == "<0.5"


def test_analysis_is_risk_only_and_flags_late_noisy_disagreement() -> None:
    result = similarity.analyze(_selected())
    assert result["positiveProductionAuthority"] is False
    assert result["riskOnlyUntilProspectiveValidation"] is True
    assert {
        "REVERSAL_BASE_RATE_UNPROVEN",
        "LATE_REVERSAL_DIRECTION_RISK",
        "MULTI_REVERSAL_PATH_NOISE",
        "LOW_MULTI_BOOK_DIRECTION_AGREEMENT",
        "HIGH_BOOK_DISPERSION",
    }.issubset(set(result["riskFlags"]))
    assert "MARKET_FLIP_REVERSAL_CANDIDATE" in result["researchCandidates"]
    assert "POSTHOC_MARKET_FLIP_2_TO_3PP_CANDIDATE" in result["researchCandidates"]
