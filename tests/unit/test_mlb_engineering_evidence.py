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


# Invalid report counters must not become false source evidence or crash the
# evidence collector. These tests perform no provider, AWS, or repository I/O.
import copy
import pytest

_COUNTERS = (
    "officialGameCount", "fundamentalsNotActiveCount", "fundamentalsShadowEvaluatedCount",
)


def _report():
    return {"scoringSummary": dict(zip(_COUNTERS, (15, 15, 0)))}


@pytest.mark.parametrize("field", _COUNTERS)
@pytest.mark.parametrize("invalid", [True, False, None, "", "NaN", "Infinity", -1, -0.5, float("nan"), float("inf"), float("-inf"), [], {}])
def test_invalid_counts_cannot_authorize_inactivity_or_raise(field, invalid):
    report = _report()
    report["scoringSummary"][field] = invalid
    assert fundamentals_feature_pipeline_gap(report) is None


@pytest.mark.parametrize("field,fractional", [
    ("officialGameCount", 15.9),
    ("fundamentalsNotActiveCount", 15.9),
    ("fundamentalsShadowEvaluatedCount", 0.9),
    ("fundamentalsShadowEvaluatedCount", -0.9),
])
def test_fractional_counts_are_not_truncated_into_valid_evidence(field, fractional):
    report = _report()
    report["scoringSummary"][field] = fractional
    assert fundamentals_feature_pipeline_gap(report) is None


@pytest.mark.parametrize("inactive", [14, 16, 100])
def test_inactivity_count_must_equal_the_official_game_count(inactive):
    report = _report()
    report["scoringSummary"]["fundamentalsNotActiveCount"] = inactive
    assert fundamentals_feature_pipeline_gap(report) is None


@pytest.mark.parametrize("values", [(15, 15, 0), (15.0, 15.0, 0.0), ("15", "15", "0"), (" 15 ", "15", "0")])
def test_integral_source_representations_remain_supported_without_mutation(values):
    report = {"scoringSummary": dict(zip(_COUNTERS, values))}
    original = copy.deepcopy(report)
    assert fundamentals_feature_pipeline_gap(report) == dict(zip(_COUNTERS, (15, 15, 0)))
    assert report == original


@pytest.mark.parametrize("field", _COUNTERS)
def test_missing_counter_is_not_inferred(field):
    report = _report()
    del report["scoringSummary"][field]
    assert fundamentals_feature_pipeline_gap(report) is None


@pytest.mark.parametrize("value", [None, [], "unparsed", 15])
def test_malformed_report_does_not_crash_or_authorize_a_gap(value):
    assert fundamentals_feature_pipeline_gap(value) is None
