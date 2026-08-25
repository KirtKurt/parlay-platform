#!/usr/bin/env python3
from __future__ import annotations

import copy
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_accuracy_target_policy_v1 as accuracy_policy
import mlb_ml_autonomy_chain_v1 as autonomy_v2
import mlb_ml_aws_training_v1 as training_v2
import mlb_ml_champion_challenger_v1 as legacy_champion
import mlb_ml_champion_runtime_v1 as legacy_runtime
import mlb_ml_experiment_v2 as experiment_v2
import mlb_ml_promotion_policy_v2 as promotion_v2


def _threshold() -> dict:
    return {
        "ok": True,
        "threshold": 0.7,
        "selectionSource": "validation_only",
    }


def _legacy_runtime_row() -> dict:
    return {
        "id": "game-1",
        "gameId": "game-1",
        "homeTeam": "Home",
        "awayTeam": "Away",
        "predictedWinner": "Home",
        "predictedSide": "home",
        "actionablePick": False,
        "playable": False,
        "homeSignal": {"score": 25.0, "americanOdds": -110},
        "awaySignal": {"score": 80.0, "americanOdds": -110},
        "frozenFeatureVector": {
            "version": "legacy-diagnostic-vector",
            "features": {
                "homeMarketProb": 0.55,
                "awayMarketProb": 0.45,
                "selectedScore": 25.0,
            },
        },
    }


def _eligible_v2_bundle(manifest: dict) -> dict:
    threshold = _threshold()
    return {
        "ok": True,
        "experimentId": manifest["experimentId"],
        "experimentManifestDigest": manifest["manifestDigest"],
        "featureSchemaFingerprint": manifest["featureSchemaFingerprint"],
        "testWasUntouchedDuringFitAndThresholdSelection": True,
        "split": {
            "counts": {"train": 300, "validation": 100, "prospectiveTest": 100}
        },
        "outcomeModel": {"ok": True},
        "reliabilityModel": {
            "ok": True,
            "thresholdSelectedOnValidationOnly": True,
            "selectedThreshold": threshold,
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
                "accuracyPct": 61.0,
                "accuracyLiftPctPoints": 1.5,
                "brierSkillPct": 2.0,
                "logLoss": 0.61,
                "calibrationError": 0.05,
                "baseline": {"logLoss": 0.65},
                "pairedAccuracyRegression": {
                    "ok": True,
                    "statisticallySignificantRegression": False,
                    "regressionPValue": 1.0,
                },
            },
            "selectedReliability": {"count": 100, "calibrationError": 0.05},
        },
    }


def main() -> int:
    installed = accuracy_policy.install()
    assert installed["ok"] is True, installed
    assert installed["automaticPromotionAfterApplicableGates"] is True
    assert installed["firstPromotionRequiresManualReview"] is False
    assert installed["learningContinuesBelowAspirationalAccuracy"] is True
    assert installed["aspirationalAccuracyBlocksTraining"] is False
    assert installed["aspirationalAccuracyBlocksCandidateEvaluation"] is False
    assert installed["aspirationalAccuracyBlocksPlayableAuthority"] is True
    assert installed["rolling24hAccuracyAffectsPromotion"] is False
    assert installed["legacyV1AuthorityEnabled"] is False
    assert legacy_champion.AUTO_PROMOTE is False

    # Legacy V1 remains permanently inert even if a stale payload exists.
    legacy_payload = {
        "directionAuthorityEnabled": True,
        "playabilityAuthorityEnabled": True,
        "outcomeModel": {"ok": True, "version": "legacy-outcome"},
        "reliabilityModel": {
            "ok": True,
            "version": "legacy-reliability",
            "thresholdSelectedOnValidationOnly": True,
            "selectedThreshold": _threshold(),
        },
    }
    original_loader = legacy_runtime.champion_store.load_champion
    original_score = legacy_runtime.dual_model.score
    previous_legacy_gate = os.environ.get("INQSI_MLB_LEGACY_V1_AUTHORITY_ENABLED")
    try:
        os.environ["INQSI_MLB_LEGACY_V1_AUTHORITY_ENABLED"] = "false"
        legacy_runtime.champion_store.load_champion = lambda: legacy_payload
        legacy_runtime.dual_model.score = lambda record, model: (
            0.2 if model.get("version") == "legacy-outcome" else 0.99
        )
        row = _legacy_runtime_row()
        result = legacy_runtime.enhance_result({"predictions": [row]})
        persisted = result["predictions"][0]
        assert persisted["predictedSide"] == "home", persisted
        assert persisted["predictedWinner"] == "Home", persisted
        assert persisted["playable"] is False, persisted
        runtime = result["mlOptimizationRuntime"]
        assert runtime["legacyV1AuthorityEnabled"] is False, runtime
        assert runtime["directionAuthorityEnabled"] is False, runtime
        assert runtime["playabilityAuthorityEnabled"] is False, runtime
        assert runtime["shadowOnly"] is True, runtime
        assert (
            "legacy_v1_authority_disabled_v2_shadow_manual_first"
            in runtime["authoritySafetyErrors"]
        )
    finally:
        legacy_runtime.champion_store.load_champion = original_loader
        legacy_runtime.dual_model.score = original_score
        if previous_legacy_gate is None:
            os.environ.pop("INQSI_MLB_LEGACY_V1_AUTHORITY_ENABLED", None)
        else:
            os.environ["INQSI_MLB_LEGACY_V1_AUTHORITY_ENABLED"] = previous_legacy_gate

    features = [f"feature{i}" for i in range(8)]
    manifest = experiment_v2.new_manifest(
        experiment_id="promotion-safety-v2",
        release_contract_id="promotion-safety-contract-v1",
        release_cutoff_utc="2026-07-22T04:00:00+00:00",
        feature_vector_version="test-vector-v2",
        feature_names=features,
    )
    manifest["prospectiveTestSealed"] = True
    for name, count in (("train", 300), ("validation", 100), ("prospectiveTest", 100)):
        manifest["partitions"][name]["rowCount"] = count
        manifest["partitions"][name]["frozen"] = True
    manifest["manifestDigest"] = experiment_v2.manifest_digest(manifest)
    bundle = _eligible_v2_bundle(manifest)

    # Install the canonical autonomy wrapper. The underlying V2 gate remains
    # unchanged; only the manual-first activation restriction is removed after
    # the runtime consumer is installed.
    autonomy_v2.install(training_v2)
    previous_consumer_gate = os.environ.get("INQSI_MLB_V2_INFERENCE_ENABLED")
    try:
        os.environ["INQSI_MLB_V2_INFERENCE_ENABLED"] = "true"
        first = training_v2.promotion_policy.evaluate(
            bundle,
            manifest,
            current_champion=None,
            automatic_promotion_enabled=True,
        )
        assert first["directionPromotionEligible"] is True, first
        assert first["playabilityPromotionEligible"] is True, first
        assert first["promotionDecision"] == "AUTO_SHADOW_APPROVAL_ELIGIBLE", first
        assert first["firstPromotionRequiresManualReview"] is False, first
        assert first["manualReviewRequired"] is False, first
        assert first["runtimeAuthorityActivationEligible"] is True, first
        assert first["learningContinuesBelowAspirationalAccuracy"] is True, first
        assert first["aspirationalAccuracyBlocksTraining"] is False, first
        assert first["aspirationalAccuracyBlocksCandidateEvaluation"] is False, first
        assert first["aspirationalAccuracyBlocksPlayabilityAuthority"] is True, first
        assert first["aspirationalDashboard"]["affectsPromotion"] is False, first

        # A challenger below the 90% aspiration can still be trained and
        # evaluated. It remains eligible only because it passes the fixed market
        # skill, calibration, proper-scoring, and prospective evidence gates.
        below_aspiration = copy.deepcopy(bundle)
        below_aspiration["prospectiveTest"]["outcome"]["accuracyPct"] = 55.0
        still_eligible = training_v2.promotion_policy.evaluate(
            below_aspiration,
            manifest,
            current_champion=None,
            automatic_promotion_enabled=True,
        )
        assert still_eligible["directionPromotionEligible"] is True, still_eligible
        assert still_eligible["promotionDecision"] == (
            "AUTO_SHADOW_APPROVAL_ELIGIBLE"
        ), still_eligible
        assert still_eligible["aspirationalDashboard"]["targetMet"] is False
        assert still_eligible["learningContinuesBelowAspirationalAccuracy"] is True
    finally:
        if previous_consumer_gate is None:
            os.environ.pop("INQSI_MLB_V2_INFERENCE_ENABLED", None)
        else:
            os.environ["INQSI_MLB_V2_INFERENCE_ENABLED"] = previous_consumer_gate

    # GitHub has no manual promotion/write path. The deployed AWS trainer owns
    # automatic compare-and-swap activation after the complete gate passes.
    workflow = (ROOT / ".github/workflows/mlb-ml-promote-champion.yml").read_text(
        encoding="utf-8"
    )
    assert "aws-actions/configure-aws-credentials" not in workflow
    assert "promote_mlb_ml_champion.py" not in workflow
    assert "contents: read" in workflow

    print(
        "MLB ML promotion safety verified: legacy V1 authority is inert; learning and challenger evaluation continue below the 90% aspiration; GitHub has no promotion write path; and deployed AWS V2 may activate a champion automatically only after immutable prospective, market-skill, calibration, proper-scoring, deployment-identity, and runtime-consumer gates pass. Automatic wagering remains disabled."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
