from mlb_v8_historical_context_eligibility_v2 import (
    MATERIALIZER_VERSION,
    VERSION,
    apply_to_snapshot,
    evaluate,
    summarize_batch,
)

LOCK = "2026-08-05T12:00:00+00:00"


def envelope(data, *, at="2026-08-05T11:00:00+00:00", verified=True, error=None):
    return {
        "data": data,
        "meta": {
            "complete": True,
            "pointInTimeProjectionVerified": verified,
            "asOfUtc": at,
            "source": "official_mlb_prior_context",
        },
        "error": error,
    }


def complete_resources():
    return {
        "pitchers": envelope({"home": {}, "away": {}}),
        "bullpens": envelope({"home": {}, "away": {}}),
        "team_context": envelope({"home": {}, "away": {}}),
        "lineups": envelope({"home": {}, "away": {}}),
        "injuries": envelope({}, verified=True),
        "park": envelope({"runFactor": 1.0}),
        "weather": envelope({"runFactor": 1.0}),
    }


def test_optional_domain_missing_does_not_discard_core_row():
    resources = complete_resources()
    resources["lineups"] = {"data": None, "meta": {}, "error": "missing"}
    result = evaluate(resources, LOCK)
    assert result["trainingEligible"] is True
    assert result["featureEligibility"]["lineups"] is False
    assert "lineups_resource_unavailable" in result["eligibilityWarnings"]


def test_core_domain_missing_fails_closed():
    resources = complete_resources()
    resources["pitchers"] = {"data": None, "meta": {}, "error": "missing"}
    result = evaluate(resources, LOCK)
    assert result["trainingEligible"] is False
    assert "pitchers_resource_unavailable" in result["eligibilityErrors"]


def test_after_lock_evidence_is_rejected():
    resources = complete_resources()
    resources["bullpens"] = envelope(
        {"home": {}, "away": {}}, at="2026-08-05T12:00:02+00:00"
    )
    result = evaluate(resources, LOCK)
    assert result["trainingEligible"] is False
    assert "bullpens_source_effective_time_after_lock" in result["eligibilityErrors"]


def test_unverified_projection_is_rejected():
    resources = complete_resources()
    resources["team_context"] = envelope(
        {"home": {}, "away": {}}, verified=False
    )
    result = evaluate(resources, LOCK)
    assert result["trainingEligible"] is False
    assert (
        "team_context_point_in_time_projection_unverified"
        in result["eligibilityErrors"]
    )


def test_missing_optional_values_are_cleared_not_imputed_as_zero():
    resources = complete_resources()
    resources["injuries"] = {"data": None, "meta": {}, "error": "missing"}
    snapshot = {
        "home": {"lineupAbsenceImpact": 0.0},
        "away": {"lineupAbsenceImpact": 0.0},
        "parkRunFactor": 1.1,
        "weatherRunFactor": 1.2,
    }
    output = apply_to_snapshot(snapshot, resources, LOCK)
    assert output["trainingEligible"] is True
    assert output["home"]["lineupAbsenceImpact"] is None
    assert output["away"]["lineupAbsenceImpact"] is None
    assert output["eligibilityPolicyVersion"] == VERSION
    assert output["materializerVersion"] == MATERIALIZER_VERSION


def test_batch_summary_publishes_reason_histogram_and_domain_coverage():
    resources = complete_resources()
    good = evaluate(resources, LOCK)
    resources["lineups"] = {"data": None, "meta": {}, "error": "missing"}
    partial = evaluate(resources, LOCK)
    summary = summarize_batch({"1": good, "2": partial})
    assert summary["diagnosedGameCount"] == 2
    assert summary["coreEligibleGameCount"] == 2
    assert summary["domainCoverage"]["lineups"]["eligibleGameCount"] == 1
    assert summary["eligibilityReasonCounts"]["lineups_resource_unavailable"] == 1
    assert summary["eligibilityReasonsByGame"]["2"]
