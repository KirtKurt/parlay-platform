#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_accuracy_target_policy_v1 as target_policy
import mlb_official_prediction_semantics as semantics
import mlb_real_world_accuracy_patch as accuracy
import mlb_real_world_accuracy_semantics_fix as accuracy_fix


installed = target_policy.install()
assert installed.get("ok") is True, installed
accuracy_fix.apply(accuracy)


def main() -> int:
    locked = {
        "slatePredictionLock": {"locked": True, "lockStatus": "LOCKED"},
        "predictions": [
            {
                "gameId": "below-60-locked-diagnostic",
                "predictedWinner": "Washington Nationals",
                "predictedSide": "home",
                "teamWinProbabilityPct": 59.99,
                "winProbabilityPct": 59.99,
                "actionablePick": True,
                "tags": ["SLATE_LOCKED", "ACTIONABLE_PICK"],
            },
            {
                "gameId": "at-60-official-not-playable",
                "predictedWinner": "New York Mets",
                "predictedSide": "away",
                "teamWinProbabilityPct": 60.0,
                "winProbabilityPct": 60.0,
                "actionablePick": False,
                "tags": ["SLATE_LOCKED", "NOT_PLAYABLE"],
            },
            {
                "gameId": "above-60-official-playable",
                "predictedWinner": "Los Angeles Dodgers",
                "predictedSide": "home",
                "teamWinProbabilityPct": 64.0,
                "winProbabilityPct": 64.0,
                "actionablePick": True,
                "tags": ["SLATE_LOCKED", "ACTIONABLE_PICK"],
            },
        ],
    }
    result = semantics.enhance_result(locked)
    below, at_floor, playable = result["predictions"]

    assert result["officialPredictionCount"] == 2
    assert result["officialPickCount"] == 2
    assert result["playablePredictionCount"] == 1
    assert result["nonPlayableOfficialPredictionCount"] == 1
    assert len(result["officialPredictionDisplay"]) == 2
    assert len(result["playablePredictionDisplay"]) == 1
    assert len(result["nonPlayableOfficialPredictionDisplay"]) == 1

    assert below["officialPrediction"] is False
    assert below["officialPick"] is False
    assert below["playable"] is False
    assert below["actionablePick"] is False
    assert below["accuracyTargetEligible"] is False
    assert below["officialPredictionStatus"] == "LOCKED_DIAGNOSTIC_BELOW_60PCT"
    assert below["recommendationStatus"] == "LOCKED_PREDICTION_BELOW_60PCT_NOT_OFFICIAL"
    assert "BELOW_60PCT_GAME_LOCK_FLOOR" in below["tags"]
    assert "OFFICIAL_LOCKED_PREDICTION" not in below["tags"]

    assert at_floor["officialPrediction"] is True
    assert at_floor["officialPick"] is True
    assert at_floor["playable"] is False
    assert at_floor["officialPredictionStatus"] == "OFFICIAL_LOCKED_PREDICTION"
    assert at_floor["individualGameLockProbabilityPct"] == 60.0
    assert at_floor["individualGameLockEligible"] is True

    assert playable["officialPrediction"] is True
    assert playable["officialPick"] is True
    assert playable["playable"] is True
    assert playable["recommendationStatus"] == "PLAYABLE_PREDICTION"

    pre_lock = semantics.enhance_result(
        {
            "slatePredictionLock": {"locked": False},
            "predictions": [
                {
                    "gameId": "pre-lock",
                    "predictedWinner": "New York Mets",
                    "teamWinProbabilityPct": 70.0,
                    "actionablePick": False,
                }
            ],
        }
    )
    row = pre_lock["predictions"][0]
    assert row["officialPrediction"] is False
    assert row["officialPick"] is False
    assert row["recommendationStatus"] == "PRE_LOCK_PREDICTION"

    audit_rows = [
        {
            "status": "GRADED",
            "id": "official-not-playable",
            "slateDateEt": "2026-07-13",
            "commenceTime": "2026-07-13T18:00:00Z",
            "homeTeam": "New York Mets",
            "awayTeam": "Washington Nationals",
            "winner": "New York Mets",
            "predictedWinner": "New York Mets",
            "predictedSide": "home",
            "correct": True,
            "officialPrediction": True,
            "officialPick": True,
            "actionablePick": False,
            "accuracyTargetEligible": False,
            "recommendationStatus": "OFFICIAL_PREDICTION_NOT_PLAYABLE",
            "predictionSemanticsVersion": semantics.VERSION,
            "tags": ["FINAL_LOCKED", "NOT_PLAYABLE"],
            "teamWinProbabilityPct": 60.0,
            "americanOdds": -125,
            "homeSignal": {"marketConsensusProbability": 0.60, "americanOdds": -125},
            "awaySignal": {"marketConsensusProbability": 0.40, "americanOdds": 115},
        },
        {
            "status": "GRADED",
            "id": "official-playable",
            "slateDateEt": "2026-07-13",
            "commenceTime": "2026-07-13T19:00:00Z",
            "homeTeam": "Los Angeles Dodgers",
            "awayTeam": "Arizona Diamondbacks",
            "winner": "Arizona Diamondbacks",
            "predictedWinner": "Los Angeles Dodgers",
            "predictedSide": "home",
            "correct": False,
            "officialPrediction": True,
            "officialPick": True,
            "actionablePick": True,
            "accuracyTargetEligible": True,
            "recommendationStatus": "PLAYABLE_PREDICTION",
            "predictionSemanticsVersion": semantics.VERSION,
            "tags": ["FINAL_LOCKED", "ACTIONABLE_PICK"],
            "teamWinProbabilityPct": 64.0,
            "americanOdds": -110,
            "homeSignal": {"marketConsensusProbability": 0.64, "americanOdds": -110},
            "awaySignal": {"marketConsensusProbability": 0.36, "americanOdds": 100},
        },
    ]
    normalized = [accuracy._normalize_audit_row(row) for row in audit_rows]
    metrics = accuracy._window_metrics(normalized)
    assert metrics["officialPredictions"]["count"] == 2
    assert metrics["playableRecommendations"]["count"] == 1
    assert metrics["officialPredictions"]["accuracyPct"] == 50.0
    assert metrics["playableRecommendations"]["accuracyPct"] == 0.0
    assert metrics["officialPredictions"]["probabilityScoring"]["brierScore"] is not None
    assert metrics["officialPredictions"]["roi"]["pricedPickCount"] == 2

    print(
        "MLB official semantics verified: 60% selected-team probability is the individual-game official-lock floor; "
        "sub-60% rows remain visible immutable diagnostics."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
