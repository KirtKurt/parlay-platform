"""Fail-closed MLB V8 model-bundle persistence and real-time shadow inference.

This module intentionally never changes production authority.  It provides a stable,
content-addressed contract between the recurring supervised trainer and the live V8
collector/scorer.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

BUNDLE_VERSION = "MLB-V8-MODEL-BUNDLE-v1"
SCHEMA_VERSION = "MLB-V8-FEATURE-SCHEMA-v1"


class V8ModelRuntimeError(RuntimeError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def build_bundle(training_report: Mapping[str, Any]) -> dict[str, Any]:
    """Extract the selected deployable model from a supervised training report.

    The trainer may expose the selected model under ``model`` or ``selection.model``.
    Missing coefficients/calibration are treated as a hard failure; market consensus
    is never substituted for a trained probability.
    """
    selection = training_report.get("selection") or {}
    model = training_report.get("model") or selection.get("model") or {}
    features = model.get("featureNames") or model.get("features") or selection.get("featureNames")
    coefficients = model.get("coefficients") or model.get("weights")
    intercept = model.get("intercept")
    if not isinstance(features, Sequence) or isinstance(features, (str, bytes)) or not features:
        raise V8ModelRuntimeError("selected V8 model has no feature names")
    if not isinstance(coefficients, Sequence) or isinstance(coefficients, (str, bytes)):
        raise V8ModelRuntimeError("selected V8 model has no coefficients")
    if len(features) != len(coefficients):
        raise V8ModelRuntimeError("V8 feature/coefficient length mismatch")
    if intercept is None:
        raise V8ModelRuntimeError("selected V8 model has no intercept")
    payload = {
        "bundleVersion": BUNDLE_VERSION,
        "featureSchemaVersion": str(model.get("featureSchemaVersion") or SCHEMA_VERSION),
        "trainingReportDigest": _digest(training_report),
        "trainedAtUtc": training_report.get("createdAtUtc") or datetime.now(timezone.utc).isoformat(),
        "featureNames": [str(x) for x in features],
        "coefficients": [float(x) for x in coefficients],
        "intercept": float(intercept),
        "calibration": model.get("calibration") or selection.get("calibration") or {"type": "identity"},
        "selectedFeatureGroup": selection.get("selectedFeatureGroup"),
        "authority": "SHADOW_ONLY",
        "productionAuthorityChanged": False,
    }
    payload["modelDigest"] = _digest(payload)
    return payload


def verify_bundle(bundle: Mapping[str, Any]) -> None:
    required = {"bundleVersion", "featureSchemaVersion", "featureNames", "coefficients", "intercept", "modelDigest"}
    missing = sorted(required.difference(bundle))
    if missing:
        raise V8ModelRuntimeError(f"V8 model bundle missing fields:{','.join(missing)}")
    candidate = dict(bundle)
    digest = str(candidate.pop("modelDigest"))
    if _digest(candidate) != digest:
        raise V8ModelRuntimeError("V8 model bundle digest mismatch")
    if bundle.get("authority") != "SHADOW_ONLY" or bundle.get("productionAuthorityChanged") is not False:
        raise V8ModelRuntimeError("V8 model bundle attempted to change production authority")


def _calibrate(probability: float, calibration: Mapping[str, Any]) -> float:
    kind = str(calibration.get("type") or "identity").lower()
    if kind == "identity":
        return probability
    if kind in {"platt", "logistic"}:
        a = float(calibration.get("a", 1.0)); b = float(calibration.get("b", 0.0))
        z = a * math.log(max(probability, 1e-9) / max(1.0 - probability, 1e-9)) + b
        return 1.0 / (1.0 + math.exp(-max(min(z, 40.0), -40.0)))
    raise V8ModelRuntimeError(f"unsupported V8 calibration type:{kind}")


def score(bundle: Mapping[str, Any], feature_vector: Mapping[str, Any]) -> dict[str, Any]:
    verify_bundle(bundle)
    missing = [name for name in bundle["featureNames"] if feature_vector.get(name) is None]
    if missing:
        raise V8ModelRuntimeError("live V8 feature vector incomplete:" + ",".join(missing))
    z = float(bundle["intercept"])
    contributions: dict[str, float] = {}
    for name, weight in zip(bundle["featureNames"], bundle["coefficients"]):
        contribution = float(weight) * float(feature_vector[name])
        z += contribution
        contributions[str(name)] = contribution
    raw = 1.0 / (1.0 + math.exp(-max(min(z, 40.0), -40.0)))
    probability = _calibrate(raw, bundle.get("calibration") or {})
    return {
        "available": True,
        "authority": "SHADOW_ONLY",
        "probability": probability,
        "probabilityPct": round(probability * 100.0, 2),
        "pickSide": "home" if probability >= 0.5 else "away",
        "modelDigest": bundle["modelDigest"],
        "featureSchemaVersion": bundle["featureSchemaVersion"],
        "featureVectorDigest": _digest({name: feature_vector[name] for name in bundle["featureNames"]}),
        "featureContributions": contributions,
        "marketConsensusFallbackUsed": False,
        "productionAuthorityChanged": False,
    }
