from __future__ import annotations

import pytest

from engineering_agent.planner import validate


SAFETY_RECEIPTS = [
    "no_direct_production_deploy",
    "no_main_branch_write",
    "no_model_promotion",
    "no_secret_mutation",
    "no_other_sport_change",
]


def _task(*, implementation: list[str], evidence: list[str]) -> dict:
    return {
        "title": "Reduce Selected Reliability Calibration Error",
        "objective": "Improve MLB calibration without changing production authority.",
        "implementation": implementation,
        "likelyFiles": ["hello_world/mlb_successor_model_v2.py"],
        "acceptanceTests": [
            "prospective_selected_recommendation_count >= 100",
            "maximum_calibration_error <= 0.08",
        ],
        "safetyReceipts": SAFETY_RECEIPTS,
        "evidenceBasis": evidence,
    }


def test_rejects_calibration_tuning_against_incomplete_prospective_holdout() -> None:
    task = _task(
        implementation=[
            "Adjust calibration parameters to reduce the selected reliability error.",
            "Re-evaluate after more prospective recommendations arrive.",
        ],
        evidence=[
            "Insufficient prospective selected recommendations (36 < 100).",
            "Selected reliability calibration too high (0.172208 > 0.08).",
        ],
    )
    with pytest.raises(ValueError, match="prospective holdout is underpowered"):
        validate(task, decision_history={"decisions": []})


def test_rejects_generic_platt_isotonic_retraining_wording_when_holdout_is_short() -> None:
    task = _task(
        implementation=[
            "Analyze and adjust model outputs to improve calibration.",
            "Implement calibration techniques such as Platt scaling or isotonic regression.",
            "Retrain model with adjusted calibration parameters.",
            "Validate calibration improvement through holdout tests.",
        ],
        evidence=[
            "Selected reliability calibration error is currently 0.172208, exceeding the required threshold of <= 0.08.",
            "Current model lacks sufficient prospective selected recommendations (36 vs required 100).",
        ],
    )
    with pytest.raises(ValueError, match="prospective holdout is underpowered"):
        validate(task, decision_history={"decisions": []})


def test_rejects_mutation_or_backfill_of_prospective_evaluation_rows() -> None:
    task = _task(
        implementation=["Backfill prospective rows so the selected recommendation count reaches 100."],
        evidence=["Insufficient prospective selected recommendations (36 < 100)."],
    )
    with pytest.raises(ValueError, match="immutable holdout evidence"):
        validate(task, decision_history={"decisions": []})


def test_allows_read_only_wait_and_capture_diagnostics_without_tuning() -> None:
    task = _task(
        implementation=[
            "Keep the current calibrator frozen while prospective rows accumulate chronologically.",
            "Add read-only diagnostics that verify each new prospective row was captured before first pitch.",
        ],
        evidence=["Insufficient prospective selected recommendations (36 < 100)."],
    )
    validated = validate(task, decision_history={"decisions": []})
    assert validated["noModelPromotion"] is True
    assert validated["noDirectProductionDeploy"] is True
