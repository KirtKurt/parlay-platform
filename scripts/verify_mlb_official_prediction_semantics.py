#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_official_prediction_semantics as semantics


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

    print("MLB official prediction/playability semantics verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
