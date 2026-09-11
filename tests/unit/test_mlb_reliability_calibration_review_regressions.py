from __future__ import annotations

from pathlib import Path

import pytest

import mlb_reliability_calibration_v1 as calibration
import mlb_reliability_calibration_selection_v1 as selection


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "mlb-reliability-calibration-contract.yml"
AUDIT = ROOT / "scripts" / "mlb_reliability_calibration_shadow_audit.py"


def test_probability_endpoints_are_valid_and_clamped_while_out_of_range_fails() -> None:
    assert calibration._clip_probability(0.0) == calibration.PROBABILITY_FLOOR
    assert calibration._clip_probability(1.0) == 1.0 - calibration.PROBABILITY_FLOOR
    for invalid in (-0.01, 1.01):
        with pytest.raises(ValueError):
            calibration._clip_probability(invalid)


def test_single_class_nested_fit_falls_back_to_identity_without_aborting() -> None:
    rows = []
    for day_index, day in enumerate(("2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04")):
        for game_index in range(10):
            if day_index < 2:
                outcome = 1
            else:
                outcome = game_index % 2
            rows.append(
                {
                    "gameId": f"{day}-{game_index}",
                    "slateDateEt": day,
                    "reliabilityProbability": 0.7,
                    "pickCorrect": outcome,
                }
            )

    calibrator, proof = selection.select_no_harm_calibrator(rows)

    assert proof["selectedStrategy"] == "identity_no_harm_fallback"
    assert proof["fallbackReason"] == "single_class_nested_fit"
    assert proof["selectedL2"] is None
    assert calibrator.slope == 1.0
    assert calibrator.intercept == 0.0
    assert calibrator.l2 == 0.0
    assert proof["laterValidationUsedForCalibrationSelection"] is False
    assert proof["prospectiveQualificationEvidence"] is False
    assert proof["productionAuthorityChanged"] is False


def test_calibration_workflow_is_secretless_on_prs_and_audit_is_main_push_only() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_dispatch" not in text
    assert "if: github.event_name == 'push' && github.ref == 'refs/heads/main'" in text
    for dependency in (
        "hello_world/mlb_ml_dual_model_v2.py",
        "hello_world/mlb_ml_walk_forward_v2.py",
        "scripts/mlb_challenger_benchmark.py",
        "tests/unit/test_mlb_reliability_calibration_review_regressions.py",
    ):
        assert text.count(dependency) >= 2
    shadow_job = text.split("  shadow-audit:", 1)[1]
    assert "secrets.AWS_ACCESS_KEY_ID" in shadow_job
    assert "secrets.AWS_SECRET_ACCESS_KEY" in shadow_job
    verify_job = text.split("  verify:", 1)[1].split("  shadow-audit:", 1)[0]
    assert "secrets." not in verify_job


def test_shadow_audit_keeps_fixed_thirty_row_later_validation_minimum() -> None:
    text = AUDIT.read_text(encoding="utf-8")
    assert "minimum_selected=30" in text
    assert "minimum_selected=min(30" not in text
