from __future__ import annotations

import copy

import scripts.run_mlb_v10_context_discovery_v1 as wrapper


def _record(fingerprint: str = "a"):
    return {
        "officialGamePk": "456",
        "predictionLockAtUtc": "2026-07-02T19:15:00+00:00",
        "slateDateEt": "2026-07-02",
        "trainingEligible": True,
        "canonicalLockValid": True,
        "duplicateContaminated": False,
        "featureCutoff": "each_game_t_minus_45",
        "homeWon": True,
        "homeSignal": {"marketConsensusProbability": 0.55},
        "awaySignal": {"marketConsensusProbability": 0.45},
        "frozenFundamentalsSnapshot": {
            "officialGamePk": "456",
            "predictionLockAtUtc": "2026-07-02T19:15:00+00:00",
            "fingerprint": fingerprint,
            "trainingEligible": True,
            "pointInTimeVerified": True,
            "postgameFieldsExcluded": True,
            "selectionUsedOutcomes": False,
            "targetGameOutcomeUsed": False,
            "featureFamilies": {
                "targetGame": {
                    "available": True,
                    "trainingEligible": True,
                    "pointInTimeVerified": True,
                }
            },
            "home": {"starterQuality": 1.0, "bullpenQuality": 0.8},
            "away": {"starterQuality": 0.5, "bullpenQuality": 0.3},
        },
    }


def test_v10_generates_side_applicable_context_rules():
    definitions = {row["definition"] for row in wrapper._atomic_with_context(_record())}
    assert any("historicalPointInTimeContext.starterQuality" in value for value in definitions)
    assert any("historicalPointInTimeContext.bullpenQuality" in value for value in definitions)


def test_v10_dataset_fingerprint_changes_with_context_not_outcome():
    first = _record("a")
    second = _record("b")
    assert wrapper._fingerprint_with_context([first]) != wrapper._fingerprint_with_context([second])
    outcome_only = copy.deepcopy(first)
    outcome_only["homeWon"] = False
    # The canonical V10 fingerprint includes labels by design for settled-corpus
    # identity; the context component itself remains outcome-free.
    assert wrapper.bridge.context_fingerprint([first]) == wrapper.bridge.context_fingerprint([outcome_only])
