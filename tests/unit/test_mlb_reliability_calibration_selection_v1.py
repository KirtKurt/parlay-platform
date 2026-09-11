from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_reliability_calibration_selection_v1 as selection


def _rows(probability: float = 0.9):
    rows = []
    for day in ("2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04"):
        for game_index in range(20):
            rows.append(
                {
                    "gameId": f"{day}-{game_index}",
                    "slateDateEt": day,
                    "reliabilityProbability": probability,
                    "pickCorrect": 1 if game_index % 2 == 0 else 0,
                }
            )
    return rows


def test_selector_uses_only_nested_earlier_validation_and_preserves_negative_authority() -> None:
    calibrator, proof = selection.select_no_harm_calibrator(_rows())
    assert calibrator.training_count == 80
    assert proof["selectionSource"] == "nested_earlier_validation_only"
    assert proof["laterValidationUsedForCalibrationSelection"] is False
    assert proof["prospectiveRowsUsedForCalibrationSelection"] == 0
    assert proof["prospectiveQualificationEvidence"] is False
    assert proof["promotionEligible"] is False
    assert proof["productionAuthorityChanged"] is False
    assert proof["innerChronologyProof"]["wholeSlatesKeptTogether"] is True


def test_selector_only_accepts_candidate_when_all_no_harm_checks_pass() -> None:
    _calibrator, proof = selection.select_no_harm_calibrator(_rows())
    eligible = [row for row in proof["candidates"] if row["eligible"]]
    for row in eligible:
        assert row["improvesCalibration"] is True
        assert row["brierNoWorse"] is True
        assert row["logLossNoWorse"] is True
    if proof["selectedStrategy"] == "regularized_logit":
        assert proof["selectedL2"] in selection.CANDIDATE_L2
        assert eligible
    else:
        assert proof["selectedStrategy"] == "identity_no_harm_fallback"
        assert proof["selectedL2"] is None
        assert not eligible


def test_identity_fallback_is_exact_when_candidate_regularization_is_forced_harmful(monkeypatch) -> None:
    original_metrics = selection._metrics
    calls = {"count": 0}

    def fake_metrics(rows, probability_key):
        calls["count"] += 1
        if calls["count"] == 1:
            return {"count": 40, "calibrationError": 0.10, "brierScore": 0.20, "logLoss": 0.60}
        return {"count": 40, "calibrationError": 0.09, "brierScore": 0.21, "logLoss": 0.61}

    monkeypatch.setattr(selection, "_metrics", fake_metrics)
    calibrator, proof = selection.select_no_harm_calibrator(_rows())
    monkeypatch.setattr(selection, "_metrics", original_metrics)
    assert proof["selectedStrategy"] == "identity_no_harm_fallback"
    assert calibrator.slope == 1.0
    assert calibrator.intercept == 0.0
    assert calibrator.l2 == 0.0
