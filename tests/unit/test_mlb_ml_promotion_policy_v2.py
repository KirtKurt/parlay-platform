import copy
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_ml_experiment_v2 as experiment
import mlb_ml_promotion_policy_v2 as policy


def manifest():
    value = experiment.new_manifest(
        experiment_id="experiment-one",
        release_contract_id="release-contract-r1",
        release_cutoff_utc="2026-07-22T04:00:00+00:00",
        feature_vector_version="vector-v2",
        model_feature_schemas={
            "outcome": [f"o{i}" for i in range(8)],
            "reliability": [f"r{i}" for i in range(8)],
        },
    )
    value["prospectiveTestSealed"] = True
    value["phase"] = "PROSPECTIVE_TEST_SEALED_AWAITING_EVALUATION"
    value["manifestDigest"] = experiment.manifest_digest(value)
    return value


def passing_bundle(manifest_value):
    return {
        "ok": True,
        "experimentId": manifest_value["experimentId"],
        "experimentManifestDigest": manifest_value["manifestDigest"],
        "featureSchemaFingerprint": manifest_value["featureSchemaFingerprint"],
        "testWasUntouchedDuringFitAndThresholdSelection": True,
        "split": {
            "counts": {
                "train": 300,
                "validation": 100,
                "prospectiveTest": 100,
            }
        },
        "outcomeModel": {"ok": True},
        "reliabilityModel": {
            "ok": True,
            "thresholdSelectedOnValidationOnly": True,
            "selectedThreshold": {
                "ok": True,
                "threshold": 0.61,
                "selectionSource": "validation_only",
            },
        },
        "prospectiveSelectedRecommendationCount": 100,
        "prospectiveSelectionLedger": {
            "ok": True,
            "settledSelectedRecommendationCount": 100,
            "conflicts": [],
        },
        "prospectiveTest": {
            "outcome": {
                "count": 100,
                "accuracyPct": 89.0,
                "accuracyLiftPctPoints": 2.0,
                "brierSkillPct": 3.0,
                "logLoss": 0.62,
                "calibrationError": 0.07,
                "baseline": {"logLoss": 0.65},
                "pairedAccuracyRegression": {
                    "ok": True,
                    "statisticallySignificantRegression": False,
                    "regressionPValue": 0.8,
                },
            },
            "selectedReliability": {
                "count": 100,
                "calibrationError": 0.07,
            },
        },
    }


def codes(result, bucket):
    return {item["code"] for item in result[bucket]}


def test_ninety_percent_is_dashboard_only_and_first_shadow_approval_is_manual():
    manifest_value = manifest()
    result = policy.evaluate(
        passing_bundle(manifest_value),
        manifest_value,
        current_champion=None,
        automatic_promotion_enabled=True,
    )
    assert result["directionPromotionEligible"] is True
    assert result["playabilityPromotionEligible"] is True
    assert result["promotionDecision"] == "PENDING_MANUAL_FIRST_SHADOW_APPROVAL"
    assert result["shadowApprovalEligible"] is True
    assert result["runtimeAuthorityActivationEligible"] is False
    assert result["aspirationalDashboard"] == {
        "accuracyPct": 89.0,
        "targetAccuracyPct": 90.0,
        "targetMet": False,
        "affectsPromotion": False,
        "affectsAuthoritySuspension": False,
    }


def test_stable_champion_is_required_before_auto_replacement():
    manifest_value = manifest()
    bundle = passing_bundle(manifest_value)
    unstable = policy.evaluate(
        bundle,
        manifest_value,
        current_champion={"artifactDigest": "old", "stableChampion": False},
        automatic_promotion_enabled=True,
    )
    stable = policy.evaluate(
        bundle,
        manifest_value,
        current_champion={"artifactDigest": "old", "stableChampion": True},
        automatic_promotion_enabled=True,
    )
    assert unstable["promotionDecision"] == (
        "PENDING_MANUAL_FIRST_SHADOW_APPROVAL"
    )
    assert stable["promotionDecision"] == "AUTO_SHADOW_APPROVAL_ELIGIBLE"
    assert stable["runtimeAuthorityActivationEligible"] is False


def test_each_realistic_direction_metric_fails_closed():
    manifest_value = manifest()
    mutations = [
        ("brierSkillPct", 0.0, "NO_POSITIVE_BRIER_SKILL"),
        ("logLoss", 0.66, "LOG_LOSS_NOT_LOWER_THAN_SAME_TIME_MARKET"),
        ("calibrationError", 0.081, "CALIBRATION_ERROR_TOO_HIGH"),
        ("accuracyLiftPctPoints", 0.99, "ACCURACY_LIFT_TOO_LOW"),
    ]
    for key, value, expected in mutations:
        bundle = passing_bundle(manifest_value)
        bundle["prospectiveTest"]["outcome"][key] = value
        result = policy.evaluate(bundle, manifest_value)
        assert expected in codes(result, "directionBlockers")

    regressed = passing_bundle(manifest_value)
    regressed["prospectiveTest"]["outcome"]["pairedAccuracyRegression"].update(
        {
            "statisticallySignificantRegression": True,
            "regressionPValue": 0.01,
        }
    )
    result = policy.evaluate(regressed, manifest_value)
    assert "STATISTICALLY_SIGNIFICANT_ACCURACY_REGRESSION" in codes(
        result, "directionBlockers"
    )


def test_playability_requires_100_candidate_specific_prospective_selections():
    manifest_value = manifest()
    bundle = passing_bundle(manifest_value)
    bundle["prospectiveSelectedRecommendationCount"] = 99
    bundle["prospectiveTest"]["selectedReliability"]["count"] = 99
    result = policy.evaluate(bundle, manifest_value)
    assert result["directionPromotionEligible"] is True
    assert result["playabilityPromotionEligible"] is False
    assert "INSUFFICIENT_PROSPECTIVE_SELECTED_RECOMMENDATIONS" in codes(
        result, "playabilityBlockers"
    )


def test_playability_fails_closed_on_selection_ledger_contract_conflict():
    manifest_value = manifest()
    bundle = passing_bundle(manifest_value)
    bundle["prospectiveSelectionLedger"] = {
        "ok": False,
        "settledSelectedRecommendationCount": 100,
        "conflicts": [{"reason": "invalid_selection_contract"}],
    }

    result = policy.evaluate(bundle, manifest_value)

    assert result["directionPromotionEligible"] is True
    assert result["playabilityPromotionEligible"] is False
    assert "PROSPECTIVE_SELECTION_LEDGER_INVALID" in codes(
        result, "playabilityBlockers"
    )


def test_manifest_or_schema_mismatch_blocks_both_authorities():
    manifest_value = manifest()
    bundle = passing_bundle(manifest_value)
    bundle["featureSchemaFingerprint"] = "wrong"
    result = policy.evaluate(bundle, manifest_value)
    assert result["promotionEligible"] is False
    assert "MODEL_FEATURE_SCHEMA_FINGERPRINT_MISMATCH" in codes(
        result, "directionBlockers"
    )
    assert "MODEL_FEATURE_SCHEMA_FINGERPRINT_MISMATCH" in codes(
        result, "playabilityBlockers"
    )
