from __future__ import annotations

from engineering_agent.runtime import (
    FOCUS_DOMAINS,
    classify_task_domain,
    excluded_focus_domains,
    next_focus_domain,
    planner_attempt_budget,
    prioritized_focus_domains,
)


def test_fresh_fundamentals_pipeline_inactivity_is_a_first_class_focus() -> None:
    evidence = (
        '{"status":"FUNDAMENTALS_FEATURE_PIPELINE_INACTIVE",'
        '"evidence":{"officialGameCount":15,"fundamentalsNotActiveCount":15,'
        '"fundamentalsShadowEvaluatedCount":0}}'
    )
    assert "feature_pipeline" in FOCUS_DOMAINS
    assert prioritized_focus_domains(evidence) == ["feature_pipeline"]
    assert next_focus_domain(evidence, []) == "feature_pipeline"


def test_feature_pipeline_task_classification_is_narrow_and_explicit() -> None:
    task = {
        "title": "Repair the MLB fundamentals scoring shadow bridge",
        "objective": "Make existing source-proven snapshots reach shadow evaluation without production authority.",
        "implementation": [
            "Diagnose why the fundamentals feature pipeline remains inactive.",
            "Preserve immutable prediction records and read-only shadow evaluation.",
        ],
        "likelyFiles": ["hello_world/mlb_fundamentals_shadow_bridge.py"],
        "acceptanceTests": ["Fundamentals scoring shadow evaluation is source-bound and read-only."],
        "evidenceBasis": ["FUNDAMENTALS_FEATURE_PIPELINE_INACTIVE"],
    }
    assert classify_task_domain(task) == "feature_pipeline"


def test_rejected_feature_pipeline_domain_is_excluded_from_same_cycle() -> None:
    evidence = '{"status":"FUNDAMENTALS_FEATURE_PIPELINE_INACTIVE"}'
    rejected = [
        {
            "title": "Repair fundamentals scoring shadow bridge",
            "domain": "feature_pipeline",
            "requiredFocusDomain": "feature_pipeline",
            "reason": "blocked by existing safety validation",
        }
    ]
    assert "feature_pipeline" in excluded_focus_domains(rejected)
    assert next_focus_domain(evidence, rejected) is None


def test_adding_feature_pipeline_never_expands_six_attempt_hard_cap() -> None:
    evidence = " ".join(
        (
            "functionerror",
            "FUNDAMENTALS_FEATURE_PIPELINE_INACTIVE",
            "missing_t10_snapshots",
            "calibration_error_too_high",
            "insufficient_observed_starter",
            "insufficient_clean_rows",
        )
    )
    assert len(prioritized_focus_domains(evidence)) == 6
    assert planner_attempt_budget(99, evidence) == 6
    assert planner_attempt_budget(6, evidence) == 6


def test_feature_pipeline_signal_does_not_reclassify_ordinary_data_capture() -> None:
    task = {
        "title": "Repair missing T10 source coverage",
        "objective": "Restore pregame source capture.",
        "implementation": ["Repair snapshot ingestion."],
        "likelyFiles": ["hello_world/mlb_manual_pull.py"],
        "acceptanceTests": ["T10 snapshots are present."],
        "evidenceBasis": ["missing_t10_snapshots"],
    }
    assert classify_task_domain(task) == "data_capture"
