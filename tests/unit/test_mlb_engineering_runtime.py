from __future__ import annotations

import json

import pytest

from engineering_agent.runtime import (
    _planner_payload,
    _validate_focus_domain,
    classify_task_domain,
    next_focus_domain,
    prioritized_focus_domains,
)


def _task(title: str, objective: str, *implementation: str) -> dict:
    return {
        "title": title,
        "objective": objective,
        "implementation": list(implementation) or ["Add focused diagnostics."],
        "likelyFiles": ["hello_world/mlb_successor_runtime_v1.py"],
        "acceptanceTests": ["Existing fail-closed behavior is unchanged."],
        "safetyReceipts": [
            "no_direct_production_deploy",
            "no_main_branch_write",
            "no_model_promotion",
            "no_secret_mutation",
            "no_other_sport_change",
        ],
        "evidenceBasis": ["Current MLB evidence supports this task."],
    }


def test_classifies_completed_deployment_identity_domain() -> None:
    task = _task(
        "Bind successor health to MLB-specific deployment identity",
        "Compare the MLB runtime identity rather than unrelated repository HEAD.",
    )
    assert classify_task_domain(task) == "deployment_identity"


def test_current_blockers_prioritize_clean_cohort_before_model_tuning() -> None:
    evidence = """
    {"reason":"insufficient_clean_rows"}
    {"blockers":["NO_POSITIVE_BRIER_SKILL","CALIBRATION_ERROR_TOO_HIGH"]}
    {"successor":{"blockers":["INSUFFICIENT_OBSERVED_TEAM_CONTEXT_TRAIN"]}}
    """
    ranked = prioritized_focus_domains(evidence)
    assert ranked[0] == "clean_cohort"
    assert "challenger_model" in ranked
    assert "calibration" in ranked


def test_next_focus_skips_domain_already_rejected_this_cycle() -> None:
    evidence = (
        "insufficient_clean_rows "
        "INSUFFICIENT_OBSERVED_TEAM_CONTEXT_TRAIN "
        "CALIBRATION_ERROR_TOO_HIGH"
    )
    rejected = [
        {
            "title": "Diagnose clean cohort",
            "domain": "clean_cohort",
            "reason": "schema invalid",
        }
    ]
    assert next_focus_domain(evidence, rejected) == "challenger_model"


def test_focus_validation_rejects_cross_domain_retry() -> None:
    deployment_task = _task(
        "Bind successor health to deployment identity",
        "Repair implementation identity checks.",
    )
    with pytest.raises(ValueError, match="required=clean_cohort"):
        _validate_focus_domain(deployment_task, "clean_cohort")


def test_focus_validation_accepts_matching_clean_cohort_task() -> None:
    clean_task = _task(
        "Diagnose source-honest MLB clean-cohort admission bottlenecks",
        "Explain why immutable fundamentals snapshots are quarantined from clean rows.",
        "Add read-only admission diagnostics without rewriting locks, labels, or feature vectors.",
    )
    assert _validate_focus_domain(clean_task, "clean_cohort") == "clean_cohort"


def test_retry_prompt_carries_deterministic_focus_and_safety_receipts() -> None:
    payload = json.loads(
        _planner_payload(
            "insufficient_clean_rows",
            {"decisions": []},
            [
                {
                    "title": "Bind successor health to MLB-specific deployment identity",
                    "domain": "deployment_identity",
                    "reason": "completed",
                }
            ],
            required_focus_domain="clean_cohort",
        )
    )
    assert payload["requiredFocusDomain"] == "clean_cohort"
    assert "deployment_identity" in payload["excludedFocusDomains"]
    assert set(payload["requiredSafetyReceipts"]) == {
        "no_direct_production_deploy",
        "no_main_branch_write",
        "no_model_promotion",
        "no_secret_mutation",
        "no_other_sport_change",
    }
