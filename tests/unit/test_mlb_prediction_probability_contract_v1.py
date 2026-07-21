from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import inqsi_pull_history as history
import mlb_prediction_probability_contract_v1 as probability_contract


def _row(*, predicted_side: str = "home") -> dict:
    home_team = "Home Club"
    away_team = "Away Club"
    return {
        "gameId": "official:123",
        "gameIdentity": "official:123",
        "homeTeam": home_team,
        "awayTeam": away_team,
        "predictedSide": predicted_side,
        "predictedWinner": home_team if predicted_side == "home" else away_team,
        "homeModelWinProbability": 0.62,
        "awayModelWinProbability": 0.38,
        "homeSignal": {
            "marketProbability": 0.54,
            "score": 12.5,
            "americanOdds": -120,
            "priceBook": "fanduel",
            "priceSource": "real_book",
            "marketSide": "home",
            "tags": ["FAVORITE"],
        },
        "awaySignal": {
            "marketProbability": 0.46,
            "score": -12.5,
            "americanOdds": 110,
            "priceBook": "fanduel",
            "priceSource": "real_book",
            "marketSide": "away",
            "tags": ["UNDERDOG"],
        },
        "pickReliabilityPct": 67.0,
        "predictionSourcePullAt": "2026-07-21T20:15:00+00:00",
        "predictionSourcePullId": "pull-20-15",
        "predictionSourceCanonicalSlot": {
            "version": history.PULL_SLOT_VERSION,
            "canonicalPullFingerprint": "canonical-slot-fingerprint",
        },
        "playable": True,
        "trainingEligible": True,
    }


def test_normalizes_one_complementary_probability_authority() -> None:
    normalized = probability_contract.normalize_row(_row())

    assert normalized["predictedWinner"] == "Home Club"
    assert normalized["predictedSide"] == "home"
    assert normalized["homeModelWinProbability"] + normalized["awayModelWinProbability"] == 1.0
    assert normalized["modelWinProbability"] == 0.62
    assert normalized["marketProbability"] == 0.54
    assert normalized["signalScore"] == 12.5
    assert normalized["pickReliability"] == 0.67
    assert normalized["americanOdds"] == -120.0
    assert normalized["priceBook"] == "fanduel"
    assert normalized["probabilityCorrectionApplied"] is False
    assert probability_contract.validation_errors(normalized) == []


def test_direction_correction_is_displayed_but_fails_closed() -> None:
    normalized = probability_contract.normalize_row(_row(predicted_side="away"))

    assert normalized["predictedWinner"] == "Home Club"
    assert normalized["predictedSide"] == "home"
    assert normalized["probabilityCorrectionApplied"] is True
    assert normalized["playable"] is False
    assert normalized["trainingEligible"] is False
    assert probability_contract.CORRECTION_REASON in normalized["releaseBlockReasons"]
    assert probability_contract.CORRECTION_REASON in normalized["trainingExclusionReasons"]
    assert probability_contract.validation_errors(normalized) == []


def test_direction_correction_remains_attested_after_reapplication() -> None:
    normalized = probability_contract.normalize_row(_row(predicted_side="away"))
    repeated = probability_contract.normalize_row(normalized)

    assert repeated["probabilityCorrectionApplied"] is True
    assert repeated["preProbabilityContractPredictedSide"] == "away"
    assert repeated["playable"] is False
    assert repeated["trainingEligible"] is False
    assert probability_contract.CORRECTION_REASON in repeated[
        "trainingExclusionReasons"
    ]
    assert probability_contract.validation_errors(repeated) == []


def test_zero_american_price_is_not_valid_market_evidence() -> None:
    row = _row()
    row["homeSignal"]["americanOdds"] = 0

    normalized = probability_contract.normalize_row(row)

    assert normalized["americanOdds"] is None
    assert normalized["playable"] is False
    assert normalized["trainingEligible"] is False
    assert probability_contract.PRICE_REASON in probability_contract.validation_errors(
        normalized
    )


def test_validation_rejects_tampered_probability_and_price_binding() -> None:
    normalized = probability_contract.normalize_row(_row())
    normalized["modelWinProbability"] = 0.38
    normalized["americanOdds"] = 110

    errors = probability_contract.validation_errors(normalized)

    assert "selected_model_probability_mismatch" in errors
    assert "selected_side_price_binding_mismatch" in errors
