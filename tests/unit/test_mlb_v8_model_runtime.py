import copy
import math

import pytest

from hello_world.mlb_v8_model_runtime import (
    RESIDUAL_MODEL,
    V8ModelRuntimeError,
    build_bundle,
    score,
    verify_bundle,
)


def _report():
    return {
        "createdAtUtc": "2026-07-28T12:12:24+00:00",
        "selection": {
            "selectedFeatureGroup": "v8_all",
            "model": {
                "featureSchemaVersion": "MLB-V8-FEATURE-SCHEMA-v1",
                "featureNames": [
                    "market_home_probability",
                    "first_five_home_probability",
                ],
                "coefficients": [1.2, 0.8],
                "intercept": -1.0,
                "trainingSteps": 350,
                "calibration": {"type": "identity"},
            },
        },
    }


def _supervised_report():
    return {
        "createdAtUtc": "2026-08-05T05:00:00+00:00",
        "resultDigest": "training-result-digest",
        "architecture": {"probabilityBounds": [0.05, 0.95]},
        "selection": {"selectedFeatureGroup": "market_temporal_team"},
        "model": {
            "featureCompilerVersion": "compiler-v8",
            "featureGroup": "market_temporal_team",
            "standardizer": {
                "featureNames": ["line_velocity", "bullpen_delta"],
                "means": [0.2, -0.1],
                "scales": [0.5, 2.0],
            },
            "weights": [0.4, -0.2],
            "intercept": 0.1,
            "trainingSteps": 700,
            "calibrator": {"slope": 1.1, "intercept": -0.05},
            "modelDigest": "source-model-digest",
        },
    }


def test_bundle_is_content_addressed_and_shadow_only():
    bundle = build_bundle(_report())
    verify_bundle(bundle)
    assert bundle["authority"] == "SHADOW_ONLY"
    assert bundle["automaticWagerAllowed"] is False
    assert bundle["productionAuthorityChanged"] is False
    assert len(bundle["modelDigest"]) == 64


def test_live_scoring_uses_model_and_never_market_fallback():
    result = score(
        build_bundle(_report()),
        {
            "market_home_probability": 0.60,
            "first_five_home_probability": 0.57,
        },
    )
    assert result["available"] is True
    assert result["marketConsensusFallbackUsed"] is False
    assert result["modelDigest"]
    assert 0.0 < result["probability"] < 1.0


def test_supervised_bundle_preserves_standardizer_market_offset_and_calibrator():
    bundle = build_bundle(_supervised_report())
    verify_bundle(bundle)

    result = score(
        bundle,
        {
            "market_home_probability": 0.60,
            "line_velocity": 0.7,
            "bullpen_delta": 1.9,
        },
    )

    residual = 0.3
    market_logit = math.log(0.60 / 0.40)
    raw = 1.0 / (1.0 + math.exp(-(market_logit + residual)))
    expected = 1.0 / (
        1.0 + math.exp(-(1.1 * math.log(raw / (1.0 - raw)) - 0.05))
    )

    assert bundle["modelType"] == RESIDUAL_MODEL
    assert bundle["standardizer"] == {
        "means": [0.2, -0.1],
        "scales": [0.5, 2.0],
    }
    assert result["marketOffsetUsed"] is True
    assert result["marketProbability"] == pytest.approx(0.60)
    assert result["standardizedFeatureValues"] == {
        "line_velocity": pytest.approx(1.0),
        "bullpen_delta": pytest.approx(1.0),
    }
    assert result["probability"] == pytest.approx(expected)


def test_prospectively_approved_report_reuses_exact_frozen_bundle():
    original = _supervised_report()
    frozen = build_bundle(original)
    augmented = copy.deepcopy(original)
    augmented["resultDigest"] = "different-augmented-report-digest"
    augmented["prospectiveAudit"] = {"prospectiveAuditPassed": True}
    augmented["frozenModelBundle"] = copy.deepcopy(frozen)
    augmented["frozenModelBundleDigest"] = frozen["modelDigest"]

    rebuilt = build_bundle(augmented)

    assert rebuilt == frozen
    assert rebuilt["modelDigest"] == frozen["modelDigest"]
    assert rebuilt["trainingReportDigest"] == "training-result-digest"


def test_tampered_frozen_bundle_or_pointer_fails_closed():
    report = _supervised_report()
    frozen = build_bundle(report)
    augmented = copy.deepcopy(report)
    augmented["frozenModelBundle"] = copy.deepcopy(frozen)
    augmented["frozenModelBundleDigest"] = "wrong"
    with pytest.raises(V8ModelRuntimeError, match="identity mismatch"):
        build_bundle(augmented)

    augmented["frozenModelBundleDigest"] = frozen["modelDigest"]
    augmented["frozenModelBundle"]["intercept"] = 99.0
    with pytest.raises(V8ModelRuntimeError, match="digest mismatch"):
        build_bundle(augmented)


def test_incomplete_live_vector_fails_closed():
    with pytest.raises(V8ModelRuntimeError, match="feature vector incomplete"):
        score(build_bundle(_report()), {"market_home_probability": 0.60})


def test_missing_market_offset_fails_closed_for_supervised_model():
    with pytest.raises(V8ModelRuntimeError, match="market probability"):
        score(
            build_bundle(_supervised_report()),
            {"line_velocity": 0.7, "bullpen_delta": 1.9},
        )


def test_tampered_bundle_is_rejected():
    bundle = build_bundle(_report())
    tampered = copy.deepcopy(bundle)
    tampered["intercept"] = 99.0
    with pytest.raises(V8ModelRuntimeError, match="digest mismatch"):
        verify_bundle(tampered)


def test_missing_trained_coefficients_cannot_be_market_consensus():
    report = _report()
    del report["selection"]["model"]["coefficients"]
    with pytest.raises(V8ModelRuntimeError, match="no coefficients"):
        build_bundle(report)


def test_market_baseline_cannot_be_serialized_as_learned_runtime():
    report = _supervised_report()
    report["model"]["featureGroup"] = "market_baseline"
    report["selection"]["selectedFeatureGroup"] = "market_baseline"
    with pytest.raises(V8ModelRuntimeError, match="not a learned candidate"):
        build_bundle(report)
