import copy
import pytest

from hello_world.mlb_v8_model_runtime import (
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
                "featureNames": ["market_home_probability", "first_five_home_probability"],
                "coefficients": [1.2, 0.8],
                "intercept": -1.0,
                "calibration": {"type": "identity"},
            },
        },
    }


def test_bundle_is_content_addressed_and_shadow_only():
    bundle = build_bundle(_report())
    verify_bundle(bundle)
    assert bundle["authority"] == "SHADOW_ONLY"
    assert bundle["productionAuthorityChanged"] is False
    assert len(bundle["modelDigest"]) == 64


def test_live_scoring_uses_model_and_never_market_fallback():
    result = score(build_bundle(_report()), {
        "market_home_probability": 0.60,
        "first_five_home_probability": 0.57,
    })
    assert result["available"] is True
    assert result["marketConsensusFallbackUsed"] is False
    assert result["modelDigest"]
    assert 0.0 < result["probability"] < 1.0


def test_incomplete_live_vector_fails_closed():
    with pytest.raises(V8ModelRuntimeError, match="feature vector incomplete"):
        score(build_bundle(_report()), {"market_home_probability": 0.60})


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
