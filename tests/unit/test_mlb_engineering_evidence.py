from __future__ import annotations

from engineering_agent.evidence import (
    FUNDAMENTALS_EXPECTED_GROUPS,
    fundamentals_capture_gap,
    fundamentals_feature_pipeline_gap,
)


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


def _capture_report() -> dict:
    groups = [
        {"group": "confirmed_probable_pitchers", "status": "CONNECTED"},
        {"group": "starter_quality", "status": "PARTIAL"},
        {"group": "starter_handedness_splits", "status": "PARTIAL"},
        {"group": "bullpen_availability", "status": "PARTIAL"},
        {"group": "confirmed_lineups", "status": "CONNECTED"},
        {"group": "ballpark_factors", "status": "PARTIAL"},
        {"group": "travel_rest", "status": "CONNECTED"},
    ]
    return {
        "reportType": "MLB_FUNDAMENTALS_PROVENANCE_READ_ONLY_DIAGNOSTIC",
        "readOnly": True,
        "mutatedPersistence": False,
        "immutablePredictionRewriteAllowed": False,
        "modelPromotionAllowed": False,
        "productionAuthorityChanged": False,
        "automaticWagerAllowed": False,
        "gameCount": 2,
        "contractSafeGameCount": 2,
        "contractBlockedGameCount": 0,
        "games": [
            {"gameIdentity": "1", "contractSafe": True, "groups": copy.deepcopy(groups)},
            {"gameIdentity": "2", "contractSafe": True, "groups": copy.deepcopy(groups)},
        ],
    }


def test_capture_gap_requires_contract_safe_read_only_immutable_proof() -> None:
    report = _capture_report()
    original = copy.deepcopy(report)
    gap = fundamentals_capture_gap(report)
    assert gap is not None
    assert gap["gameCount"] == 2
    assert gap["incompleteGameCount"] == 2
    assert gap["contractSafeGameCount"] == 2
    assert gap["contractBlockedGameCount"] == 0
    assert gap["automaticWagerAllowed"] is False
    assert gap["groupStatusCounts"]["confirmed_probable_pitchers"] == {"CONNECTED": 2}
    assert gap["groupStatusCounts"]["starter_quality"] == {"PARTIAL": 2}
    assert gap["groupStatusCounts"]["offense_quality"] == {"MISSING_FROM_DIAGNOSTIC_PROOF": 2}
    assert gap["groupStatusCounts"]["weather_roof"] == {"MISSING_FROM_DIAGNOSTIC_PROOF": 2}
    assert gap["groupStatusCounts"]["injuries_late_scratches"] == {"MISSING_FROM_DIAGNOSTIC_PROOF": 2}
    assert report == original


@pytest.mark.parametrize("field,bad", [
    ("readOnly", False),
    ("mutatedPersistence", True),
    ("immutablePredictionRewriteAllowed", True),
    ("modelPromotionAllowed", True),
    ("productionAuthorityChanged", True),
    ("automaticWagerAllowed", True),
])
def test_capture_gap_refuses_mutable_or_authority_bearing_reports(field, bad) -> None:
    report = _capture_report()
    report[field] = bad
    assert fundamentals_capture_gap(report) is None


def test_capture_gap_refuses_any_contract_blocked_game() -> None:
    report = _capture_report()
    report["contractSafeGameCount"] = 1
    report["contractBlockedGameCount"] = 1
    report["games"][1]["contractSafe"] = False
    assert fundamentals_capture_gap(report) is None


@pytest.mark.parametrize("field,value", [
    ("gameCount", 0),
    ("gameCount", 2.5),
    ("contractSafeGameCount", 1),
    ("contractBlockedGameCount", 1),
])
def test_capture_gap_refuses_malformed_or_inconsistent_counts(field, value) -> None:
    report = _capture_report()
    report[field] = value
    assert fundamentals_capture_gap(report) is None


def test_capture_gap_refuses_duplicate_group_evidence() -> None:
    report = _capture_report()
    report["games"][0]["groups"].append(
        {"group": "travel_rest", "status": "CONNECTED"}
    )
    assert fundamentals_capture_gap(report) is None


def test_capture_gap_returns_none_when_every_required_group_is_connected() -> None:
    report = _capture_report()
    connected = [
        {"group": group, "status": "CONNECTED"}
        for group in FUNDAMENTALS_EXPECTED_GROUPS
    ]
    for game in report["games"]:
        game["groups"] = copy.deepcopy(connected)
    assert fundamentals_capture_gap(report) is None
