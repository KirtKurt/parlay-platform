from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TARGET_PATH = ROOT / "hello_world" / "mlb_accuracy_target_patch.py"
SAFETY_PATH = ROOT / "hello_world" / "mlb_market_anchor_flip_safety_patch.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _july_16_losing_profile():
    return {
        "predictedWinner": "New York Mets",
        "predictedSide": "away",
        "score": 29.2,
        "tags": ["BOOK_AGREEMENT", "POSITIVE_MOVE", "REVERSAL", "UNDERDOG"],
        "homeSignal": {
            "team": "Philadelphia Phillies",
            "side": "home",
            "score": 8.39,
            "probLatest": 0.534004,
            "marketConsensusProbability": 0.534004,
            "delta": -0.00668,
            "reversalCount": 3,
            "runLineMovement": 0,
            "tags": ["BOOK_AGREEMENT", "FAVORITE", "NEGATIVE_MOVE", "REVERSAL"],
        },
        "awaySignal": {
            "team": "New York Mets",
            "side": "away",
            "score": 29.2,
            "probLatest": 0.465996,
            "marketConsensusProbability": 0.465996,
            "delta": 0.00668,
            "reversalCount": 3,
            "runLineMovement": 0,
            "tags": ["BOOK_AGREEMENT", "POSITIVE_MOVE", "REVERSAL", "UNDERDOG"],
        },
    }


def test_market_anchor_side_change_must_pass_flip_safety():
    target = _load(TARGET_PATH, "_test_mlb_accuracy_target_patch")
    safety = _load(SAFETY_PATH, "_test_mlb_market_anchor_flip_safety_patch")
    safety.apply(target)

    result = target.optimize_prediction(_july_16_losing_profile())

    assert result["optimizerFlipRequested"] is True
    assert result["optimizerFlipAllowed"] is False
    assert result["optimizerFlippedPick"] is False
    assert result["predictedWinner"] == "New York Mets"
    assert result["predictedSide"] == "away"
    assert result["marketAnchorTeam"] == "Philadelphia Phillies"
    assert result["marketAnchorApplied"] is True
    assert result["marketAnchorFlipSafetyApplied"] is True
    assert "flip_candidate_multiple_reversals" in result["optimizerFlipBlockedReasons"]
    assert "flip_candidate_insufficient_market_confirmation" in result["optimizerFlipBlockedReasons"]
    assert "flip_candidate_no_positive_confirmed_direction" in result["optimizerFlipBlockedReasons"]
    assert result["actionablePick"] is False
    assert result["accuracyTargetEligible"] is False


def test_existing_market_anchor_selection_does_not_require_a_flip():
    target = _load(TARGET_PATH, "_test_mlb_accuracy_target_patch_existing_anchor")
    safety = _load(SAFETY_PATH, "_test_mlb_market_anchor_flip_safety_patch_existing_anchor")
    safety.apply(target)

    row = _july_16_losing_profile()
    row["predictedWinner"] = "Philadelphia Phillies"
    row["predictedSide"] = "home"
    result = target.optimize_prediction(row)

    assert result["optimizerFlipRequested"] is False
    assert result["optimizerFlipAllowed"] is True
    assert result["predictedWinner"] == "Philadelphia Phillies"
    assert result["actionablePick"] is False
