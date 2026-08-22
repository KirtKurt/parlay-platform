from __future__ import annotations

import copy

import mlb_v8_model_runtime as runtime
import mlb_v8_observational_audit_v1_4 as audit


def _source_report():
    return {
        "createdAtUtc": "2026-08-10T20:00:00+00:00",
        "resultDigest": "training-result-digest",
        "architecture": {"probabilityBounds": [0.02, 0.98]},
        "selection": {
            "selectedFeatureGroup": "market_temporal_team",
            "selectedL2": 0.2,
        },
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
            "l2": 0.2,
            "trainingSteps": 700,
            "seed": 260726,
            "minProbability": 0.05,
            "maxProbability": 0.95,
            "calibrator": {"slope": 1.1, "intercept": -0.05},
        },
    }


def test_fitted_model_bounds_replace_source_architecture_bounds_in_runtime_bundle():
    report = _source_report()
    aligned = audit.align_runtime_report(report)
    bundle = runtime.build_bundle(aligned)
    runtime.verify_bundle(bundle)

    assert report["architecture"]["probabilityBounds"] == [0.02, 0.98]
    assert aligned["architecture"]["sourceTrainingProbabilityBounds"] == [
        0.02,
        0.98,
    ]
    assert aligned["architecture"]["probabilityBounds"] == [0.05, 0.95]
    assert aligned["architecture"]["probabilityBoundsAuthority"] == (
        "FITTED_OBSERVATIONAL_MODEL"
    )
    assert aligned["model"]["minProbability"] == 0.05
    assert aligned["model"]["maxProbability"] == 0.95
    assert bundle["probabilityBounds"] == [0.05, 0.95]


def test_missing_model_bounds_are_materialized_with_runtime_defaults():
    report = _source_report()
    report["model"].pop("minProbability")
    report["model"].pop("maxProbability")

    aligned = audit.align_runtime_report(report)
    bundle = runtime.build_bundle(aligned)
    runtime.verify_bundle(bundle)

    assert aligned["model"]["minProbability"] == 0.05
    assert aligned["model"]["maxProbability"] == 0.95
    assert aligned["architecture"]["probabilityBounds"] == [0.05, 0.95]
    assert bundle["probabilityBounds"] == [0.05, 0.95]


def test_bound_alignment_is_copy_safe_and_rejects_invalid_model_bounds():
    report = _source_report()
    original = copy.deepcopy(report)
    aligned = audit.align_runtime_report(report)

    assert report == original
    assert aligned is not report

    report["model"]["minProbability"] = 0.99
    report["model"]["maxProbability"] = 0.95
    try:
        audit.align_runtime_report(report)
    except ValueError as exc:
        assert "probability bounds are invalid" in str(exc)
    else:
        raise AssertionError("invalid observational model bounds must fail closed")
