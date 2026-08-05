from types import SimpleNamespace

import pytest

import hello_world.mlb_v8_prospective_audit_v1 as audit
from hello_world import mlb_v8_autonomy_v1 as autonomy


def _selection(group="market_temporal_team"):
    return {
        "selectedFeatureGroup": group,
        "candidateCount": 2,
        "foldCount": 3,
        "selectionGuard": {
            "thresholds": {"regularizationGrid": [0.1]},
            "learnedEligibleCandidateCount": 1,
        },
        "ablation": {
            "market_baseline": {},
            "market_temporal_team": {
                "l2": 0.1,
                "guard": {
                    "eligible": True,
                    "errors": [],
                    "stability": {
                        "positiveFoldCount": 3,
                        "overallAccuracyUplift": 0.02,
                        "meanDailyAccuracyUplift": 0.02,
                    },
                },
                "oofMetrics": {
                    "overallAccuracy": 0.82,
                    "meanDailyAccuracy": 0.82,
                    "minimumDailyAccuracy": 0.80,
                },
            },
        },
    }


def _training():
    value = {
        "ok": True,
        "createdAtUtc": "2026-08-05T05:00:00+00:00",
        "architecture": {"probabilityBounds": [0.05, 0.95]},
        "selection": _selection(),
        "model": {
            "featureCompilerVersion": "compiler-v8",
            "featureGroup": "market_temporal_team",
            "standardizer": {
                "featureNames": ["x"],
                "means": [0.0],
                "scales": [1.0],
            },
            "weights": [2.5],
            "intercept": 0.0,
            "trainingSteps": 700,
            "calibrator": {"identity": True, "slope": 1.0, "intercept": 0.0},
            "modelDigest": "source-model-digest",
        },
        "learningExecution": {
            "learningExecuted": True,
            "learnedCandidateSelected": True,
            "marketBaselineRetainedByGuard": False,
            "learnedCandidateCount": 1,
            "totalOptimizationSteps": 1360,
            "selectedFeatureGroup": "market_temporal_team",
        },
        "promotionGate": {"passed": True, "errors": []},
        "partitions": {
            "train": {"dates": ["2026-01-01"], "lastDate": "2026-01-01"},
            "walkForward": {"dates": ["2026-01-02"], "lastDate": "2026-01-02"},
            "untouchedAudit": {"dates": ["2026-01-03"], "lastDate": "2026-01-03"},
        },
        "retrospectiveArchitectureEvaluation": True,
        "freshProspectiveAuditRequired": True,
        "productionPromotionEligible": False,
        "automaticWagerAllowed": False,
        "productionAuthorityChanged": False,
    }
    value["resultDigest"] = autonomy._sha(value)
    return value


def _baseline_training():
    value = _training()
    value["selection"] = _selection("market_baseline")
    value["model"] = {
        "featureGroup": "market_baseline",
        "trainingSteps": 0,
    }
    value["learningExecution"].update(
        {
            "learnedCandidateSelected": False,
            "marketBaselineRetainedByGuard": True,
            "selectedFeatureGroup": "market_baseline",
        }
    )
    value["promotionGate"] = {
        "passed": False,
        "errors": ["learned_candidate_not_selected"],
    }
    value["resultDigest"] = autonomy._sha(
        {key: item for key, item in value.items() if key != "resultDigest"}
    )
    return value


def _examples(count=200, *, first_day=4):
    rows = []
    for index in range(count):
        day = first_day + index % 15
        rows.append(
            SimpleNamespace(
                day=f"2026-01-{day:02d}",
                game_id=str(index),
                outcome=1,
                market_probability=0.60,
                features={"x": 1.0},
            )
        )
    return rows


def test_candidate_freezes_model_and_exact_corpus_boundary():
    training = _training()
    eligibility = audit.candidate_eligibility(training)
    candidate = audit.build_candidate(training)
    audit.verify_candidate(candidate)

    assert eligibility["runtimeBundleApplicable"] is True
    assert (
        eligibility["runtimeBundleStatus"]
        == "VALID_LEARNED_CANDIDATE_BUNDLE"
    )
    assert candidate["frozenCorpusLastDate"] == "2026-01-03"
    assert candidate["modelBundle"]["selectedFeatureGroup"] == (
        "market_temporal_team"
    )
    assert candidate["modelBundle"]["trainingSteps"] == 700
    assert candidate["automaticWagerAllowed"] is False
    assert candidate["productionAuthorityChanged"] is False


def test_market_baseline_bundle_is_explicitly_not_applicable_not_invalid():
    eligibility = audit.candidate_eligibility(_baseline_training())

    assert eligibility["ok"] is False
    assert eligibility["modelBundle"] is None
    assert eligibility["runtimeBundleApplicable"] is False
    assert (
        eligibility["runtimeBundleStatus"]
        == "NOT_APPLICABLE_MARKET_BASELINE_RETAINED"
    )
    assert eligibility["selectedFeatureGroup"] == "market_baseline"
    assert "learned_candidate_not_selected" in eligibility["errors"]
    assert "market_baseline_retained" in eligibility["errors"]
    assert "retrospective_promotion_gate_not_passed" in eligibility["errors"]
    assert not any(
        error.startswith("runtime_bundle_invalid:")
        for error in eligibility["errors"]
    )


def test_learned_candidate_with_invalid_bundle_still_fails_closed():
    training = _training()
    training["model"].pop("weights")

    eligibility = audit.candidate_eligibility(training)

    assert eligibility["ok"] is False
    assert eligibility["runtimeBundleApplicable"] is True
    assert (
        eligibility["runtimeBundleStatus"]
        == "INVALID_LEARNED_CANDIDATE_BUNDLE"
    )
    assert eligibility["modelBundle"] is None
    assert any(
        error.startswith("runtime_bundle_invalid:")
        for error in eligibility["errors"]
    )


def test_prospective_audit_uses_only_later_slates_and_can_pass(monkeypatch):
    candidate = audit.build_candidate(_training())
    earlier = SimpleNamespace(
        day="2026-01-03",
        game_id="old",
        outcome=0,
        market_probability=0.90,
        features={"x": -10.0},
    )
    examples = [earlier] + _examples()
    monkeypatch.setattr(audit.features, "prepare_examples", lambda _records: examples)

    result = audit.evaluate_candidate(candidate, [{}])

    assert result["prospectiveEvidenceComplete"] is True
    assert result["prospectiveAuditPassed"] is True
    assert result["prospectiveAuditRejected"] is False
    assert result["modelMetrics"]["gameCount"] == 200
    assert result["modelMetrics"]["dayCount"] == 15
    assert result["prospectiveFirstDate"] == "2026-01-04"
    assert result["modelRefitDuringProspectiveAudit"] is False
    assert result["selectionUsedProspectiveOutcomes"] is False


def test_incomplete_prospective_evidence_keeps_collecting(monkeypatch):
    candidate = audit.build_candidate(_training())
    monkeypatch.setattr(
        audit.features, "prepare_examples", lambda _records: _examples(20)
    )

    result = audit.evaluate_candidate(candidate, [{}])

    assert result["prospectiveEvidenceComplete"] is False
    assert result["prospectiveAuditPassed"] is False
    assert result["prospectiveAuditRejected"] is False
    assert "prospective_game_floor_not_met" in result["errors"]


def test_passed_audit_produces_an_automatically_promotable_frozen_report(monkeypatch):
    candidate = audit.build_candidate(_training())
    monkeypatch.setattr(
        audit.features, "prepare_examples", lambda _records: _examples()
    )
    result = audit.evaluate_candidate(candidate, [{}])

    effective = audit.augment_training_for_promotion(candidate, result)

    assert effective["freshProspectiveAuditRequired"] is False
    assert effective["productionPromotionEligible"] is True
    assert effective["retrospectiveArchitectureEvaluation"] is False
    assert effective["learningExecution"]["learningExecuted"] is True
    assert effective["learningExecution"]["learnedCandidateSelected"] is True
    assert effective["autonomyDecision"] == "AUTO_PROMOTE_GUARDED_CHAMPION"
    assert effective["prospectiveCandidateDigest"] == candidate["candidateDigest"]
    assert effective["automaticWagerAllowed"] is False


def test_tampered_candidate_fails_closed():
    candidate = audit.build_candidate(_training())
    candidate["frozenCorpusLastDate"] = "2026-12-31"
    with pytest.raises(ValueError, match="digest mismatch"):
        audit.verify_candidate(candidate)
