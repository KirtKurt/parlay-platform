from __future__ import annotations

from engineering_agent.evidence import fundamentals_feature_pipeline_gap


def test_feature_gap_requires_positive_all_game_inactivity_and_zero_shadow_evaluation() -> None:
    report = {
        "scoringSummary": {
            "officialGameCount": 15,
            "fundamentalsNotActiveCount": 15,
            "fundamentalsShadowEvaluatedCount": 0,
        }
    }
    assert fundamentals_feature_pipeline_gap(report) == {
        "officialGameCount": 15,
        "fundamentalsNotActiveCount": 15,
        "fundamentalsShadowEvaluatedCount": 0,
    }


def test_feature_gap_does_not_trigger_from_field_names_when_pipeline_is_active() -> None:
    report = {
        "scoringSummary": {
            "officialGameCount": 15,
            "fundamentalsNotActiveCount": 0,
            "fundamentalsShadowEvaluatedCount": 15,
        }
    }
    assert fundamentals_feature_pipeline_gap(report) is None


def test_feature_gap_does_not_trigger_without_official_games() -> None:
    report = {
        "scoringSummary": {
            "officialGameCount": 0,
            "fundamentalsNotActiveCount": 0,
            "fundamentalsShadowEvaluatedCount": 0,
        }
    }
    assert fundamentals_feature_pipeline_gap(report) is None
