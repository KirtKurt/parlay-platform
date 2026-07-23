from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_reversal_precision_runtime_patch as runtime_patch


def _row() -> dict:
    return {
        "predictedSide": "home",
        "predictedWinner": "Home Club",
        "opponent": "Away Club",
        "winProbability": 0.75,
        "score": 80.0,
        "officialPrediction": False,
        "playable": True,
        "playablePick": True,
        "actionablePick": True,
        "accuracyTargetEligible": True,
        "actionability": "STRONG_ACTIONABLE_PICK",
        "playabilityStatus": "PLAYABLE",
        "recommendationStatus": "PLAYABLE_PREDICTION",
        "tags": ["ACTIONABLE_PICK", "PLAYABLE_PREDICTION"],
        "homeSignal": {"reversalCount": 0, "probLatest": 0.75},
        "awaySignal": {"reversalCount": 0, "probLatest": 0.25},
    }


def test_empty_registry_abstains_without_changing_direction() -> None:
    row = _row()
    result = runtime_patch.enforce_row(row)

    assert result["predictedSide"] == row["predictedSide"]
    assert result["predictedWinner"] == row["predictedWinner"]
    assert result["winProbability"] == row["winProbability"]
    assert result["homeSignal"] == row["homeSignal"]
    assert result["precisionQualifiedRecommendation"] is False
    assert result["precisionAdmission"]["recommendationEligible"] is False
    assert result["actionablePick"] is False
    assert result["playable"] is False
    assert result["playabilityStatus"] == "BLOCKED"
    assert "PRECISION_ADMISSION_NOT_MET" in result["tags"]
    assert "ACTIONABLE_PICK" not in result["tags"]
    assert "precision_admission_not_met" in result["pickDiscipline"]["mandatoryBlockReasons"]


def test_apply_is_idempotent_and_reports_abstention() -> None:
    module = SimpleNamespace(
        predict_all=lambda: {"predictions": [_row()], "modelVersion": "base"}
    )
    runtime_patch.apply(module)
    first = module.predict_all
    runtime_patch.apply(module)
    assert module.predict_all is first

    result = module.predict_all()
    assert result["count"] == 1
    assert result["actionablePickCount"] == 0
    assert result["noPickCount"] == 1
    assert result["precisionQualifiedRecommendationCount"] == 0
    assert result["precisionAbstainedRecommendationCount"] == 1
    assert result["accuracyTarget"]["precisionAdmissionEnforced"] is True
    assert result["modelVersion"].endswith("+reversal-precision-admission-v1")


def test_runtime_installs_precision_after_probability_actionability() -> None:
    source = (HELLO_WORLD / "mlb_ml_runtime_install_v3.py").read_text(encoding="utf-8")
    probability_call = "mlb_probability_actionability_guard.apply(engine)"
    precision_call = "mlb_reversal_precision_runtime_patch.apply(engine)"
    assert "import mlb_reversal_precision_runtime_patch" in source
    assert probability_call in source
    assert precision_call in source
    assert source.index(probability_call) < source.index(precision_call)
