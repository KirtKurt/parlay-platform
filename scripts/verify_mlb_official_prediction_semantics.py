#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_official_prediction_semantics as semantics
import mlb_real_world_accuracy_patch as accuracy


def main() -> int:
    locked = {
        "slatePredictionLock": {"locked": True, "lockStatus": "LOCKED"},
        "predictions": [
            {
                "gameId": "low-confidence-official",
                "predictedWinner": "Washington Nationals",
                "actionablePick": False,
                "officialPick": False,
                "tags": ["SLATE_LOCKED", "ML_REJECTED", "NOT_PLAYABLE"],
            },
            {
                "gameId": "playable-official",
                "predictedWinner": "Los Angeles Dodgers",
                "actionablePick": True,
                "officialPick": False,
                "tags": ["SLATE_LOCKED", "ACTIONABLE_PICK"],
            },
        ],
    }
    result = semantics.enhance_result(locked)
    first, second = result["predictions"]

    assert result["officialPredictionCount"] == 2
    assert result["officialPickCount"] == 2
    assert result["playablePredictionCount"] == 1
    assert result["nonPlayableOfficialPredictionCount"] == 1
    assert len(result["officialPredictionDisplay"]) == 2
    assert len(result["playablePredictionDisplay"]) == 1
    assert len(result["nonPlayableOfficialPredictionDisplay"]) == 1

    assert first["officialPrediction"] is True
    assert first["officialPick"] is True
    assert first["playable"] is False
    assert first["recommendationStatus"] == "OFFICIAL_PREDICTION_NOT_PLAYABLE"
    assert "OFFICIAL_LOCKED_PREDICTION" in first["tags"]
    assert "NOT_PLAYABLE" in first["tags"]

    assert second["officialPrediction"] is True
    assert second["officialPick"] is True
    assert second["playable"] is True
    assert second["recommendationStatus"] == "PLAYABLE_PREDICTION"

    pre_lock = semantics.enhance_result(
        {
            "slatePredictionLock": {"locked": False},
            "predictions": [{"gameId": "pre-lock", "predictedWinner": "New York Mets", "actionablePick": False}],
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
            "slateDateEt": "2026-07-11",
            "commenceTime": "2026-07-11T18:00:00Z",
            "homeTeam": "Washington Nationals",
            "awayTeam": "New York Yankees",
            "winner": "New York Yankees",
            "predictedWinner": "New York Yankees",
            "predictedSide": "away",
            "correct": True,
            "officialPrediction": True,
            "officialPick": True,
            "actionablePick": False,
            "accuracyTargetEligible": False,
            "recommendationStatus": "OFFICIAL_PREDICTION_NOT_PLAYABLE",
            "tags": ["SLATE_LOCKED", "ML_REJECTED", "NOT_PLAYABLE"],
            "teamWinProbabilityPct": 58.0,
            "americanOdds": -125,
            "homeSignal": {"marketConsensusProbability": 0.42, "americanOdds": 115},
            "awaySignal": {"marketConsensusProbability": 0.58, "americanOdds": -125},
        },
        {
            "status": "GRADED",
            "id": "official-playable",
            "slateDateEt": "2026-07-11",
            "commenceTime": "2026-07-11T19:00:00Z",
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
            "tags": ["SLATE_LOCKED", "ACTIONABLE_PICK"],
            "teamWinProbabilityPct": 55.0,
            "americanOdds": -110,
            "homeSignal": {"marketConsensusProbability": 0.55, "americanOdds": -110},
            "awaySignal": {"marketConsensusProbability": 0.45, "americanOdds": 100},
        },
    ]
    normalized = [accuracy._normalize_audit_row(row) for row in audit_rows]
    metrics = accuracy._window_metrics(normalized)

    assert normalized[0]["officialPrediction"] is True
    assert normalized[0]["playable"] is False
    assert normalized[1]["officialPrediction"] is True
    assert normalized[1]["playable"] is True
    assert metrics["officialPredictions"]["count"] == 2
    assert metrics["playableRecommendations"]["count"] == 1
    assert metrics["officialPredictions"]["accuracyPct"] == 50.0
    assert metrics["playableRecommendations"]["accuracyPct"] == 0.0
    assert metrics["officialPredictions"]["probabilityScoring"]["brierScore"] is not None
    assert metrics["officialPredictions"]["probabilityScoring"]["logLoss"] is not None
    assert metrics["officialPredictions"]["roi"]["pricedPickCount"] == 2
    assert metrics["marketFavoriteBaseline"]["count"] == 2
    assert metrics["comparison"]["modelAccuracyLiftVsMarketPct"] is not None

    print("MLB official prediction/playability and real-world accuracy metrics verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
