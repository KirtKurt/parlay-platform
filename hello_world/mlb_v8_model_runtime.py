"""Fail-closed MLB V8 model-bundle persistence and shadow inference.

The runtime supports the current supervised residual-logistic model exactly: frozen
feature ordering, training-time standardization, the de-vigged market probability as
an offset, and the fitted Platt calibrator.  Legacy standalone logistic bundles remain
readable for compatibility.  No bundle can change production or wagering authority.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

BUNDLE_VERSION = "MLB-V8-MODEL-BUNDLE-v2-supervised-residual"
SCHEMA_VERSION = "MLB-V8-FEATURE-SCHEMA-v2-frozen-standardizer"
RESIDUAL_MODEL = "residual_logistic_over_market_prior"
LEGACY_MODEL = "standalone_logistic"
DEFAULT_MARKET_FEATURE = "market_home_probability"


class V8ModelRuntimeError(RuntimeError):
    pass


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            default=str,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise V8ModelRuntimeError("V8 bundle contains non-canonical values") from exc


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _finite(value: Any, name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise V8ModelRuntimeError(f"invalid V8 numeric value:{name}") from exc
    if not math.isfinite(parsed):
        raise V8ModelRuntimeError(f"non-finite V8 numeric value:{name}")
    return parsed


def _sequence(value: Any, name: str, *, allow_empty: bool = False) -> list[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise V8ModelRuntimeError(f"selected V8 model has no {name}")
    values = list(value)
    if not values and not allow_empty:
        raise V8ModelRuntimeError(f"selected V8 model has no {name}")
    return values


def _calibration(model: Mapping[str, Any], selection: Mapping[str, Any]) -> dict[str, Any]:
    value = (
        model.get("calibrator")
        or model.get("calibration")
        or selection.get("calibrator")
        or selection.get("calibration")
        or {"type": "identity"}
    )
    if not isinstance(value, Mapping):
        raise V8ModelRuntimeError("selected V8 model calibration is invalid")
    if value.get("identity") is True:
        return {"type": "identity"}
    kind = str(value.get("type") or "platt").strip().lower()
    if kind == "identity":
        return {"type": "identity"}
    if kind not in {"platt", "logistic"}:
        raise V8ModelRuntimeError(f"unsupported V8 calibration type:{kind}")
    slope = value.get("slope", value.get("a", 1.0))
    intercept = value.get("intercept", value.get("b", 0.0))
    return {
        "type": "platt",
        "slope": _finite(slope, "calibration.slope"),
        "intercept": _finite(intercept, "calibration.intercept"),
    }


def _frozen_bundle(training_report: Mapping[str, Any]) -> dict[str, Any] | None:
    value = training_report.get("frozenModelBundle")
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise V8ModelRuntimeError("frozen V8 model bundle is invalid")
    bundle = copy.deepcopy(dict(value))
    verify_bundle(bundle)
    expected = str(training_report.get("frozenModelBundleDigest") or "")
    if not expected:
        raise V8ModelRuntimeError("frozen V8 model bundle digest is missing")
    if bundle.get("modelDigest") != expected:
        raise V8ModelRuntimeError("frozen V8 model bundle identity mismatch")
    return bundle


def build_bundle(training_report: Mapping[str, Any]) -> dict[str, Any]:
    """Extract a content-addressed deployable model from one training report.

    Prospectively approved reports carry the exact bundle frozen before the audit.
    That immutable bundle is returned verbatim after validation; it is never rebuilt
    from the augmented promotion report.
    """

    if not isinstance(training_report, Mapping):
        raise V8ModelRuntimeError("training report is not an object")
    frozen = _frozen_bundle(training_report)
    if frozen is not None:
        return frozen

    selection = training_report.get("selection") or {}
    if not isinstance(selection, Mapping):
        selection = {}
    model = training_report.get("model") or selection.get("model") or {}
    if not isinstance(model, Mapping):
        raise V8ModelRuntimeError("selected V8 model is invalid")

    standardizer = model.get("standardizer") or {}
    if not isinstance(standardizer, Mapping):
        standardizer = {}
    features = (
        model.get("featureNames")
        or model.get("features")
        or standardizer.get("featureNames")
        or selection.get("featureNames")
    )
    coefficients = model.get("coefficients")
    if coefficients is None:
        coefficients = model.get("weights")
    feature_names = [str(item) for item in _sequence(features, "feature names")]
    weights = [
        _finite(item, f"coefficient[{index}]")
        for index, item in enumerate(_sequence(coefficients, "coefficients"))
    ]
    if len(feature_names) != len(weights):
        raise V8ModelRuntimeError("V8 feature/coefficient length mismatch")
    if model.get("intercept") is None:
        raise V8ModelRuntimeError("selected V8 model has no intercept")

    supervised_residual = bool(standardizer) or "weights" in model
    model_type = RESIDUAL_MODEL if supervised_residual else LEGACY_MODEL
    if supervised_residual:
        means = [
            _finite(item, f"standardizer.mean[{index}]")
            for index, item in enumerate(
                _sequence(standardizer.get("means"), "standardizer means")
            )
        ]
        scales = [
            _finite(item, f"standardizer.scale[{index}]")
            for index, item in enumerate(
                _sequence(standardizer.get("scales"), "standardizer scales")
            )
        ]
        if len(means) != len(feature_names) or len(scales) != len(feature_names):
            raise V8ModelRuntimeError("V8 standardizer length mismatch")
        if any(abs(value) < 1e-12 for value in scales):
            raise V8ModelRuntimeError("V8 standardizer contains a zero scale")
    else:
        means = [0.0] * len(feature_names)
        scales = [1.0] * len(feature_names)

    bounds = (
        (training_report.get("architecture") or {}).get("probabilityBounds")
        if isinstance(training_report.get("architecture"), Mapping)
        else None
    ) or model.get("probabilityBounds") or [0.05, 0.95]
    bounds_list = _sequence(bounds, "probability bounds")
    if len(bounds_list) != 2:
        raise V8ModelRuntimeError("V8 probability bounds must have two values")
    low = _finite(bounds_list[0], "probabilityBounds[0]")
    high = _finite(bounds_list[1], "probabilityBounds[1]")
    if not (0.0 < low < high < 1.0):
        raise V8ModelRuntimeError("V8 probability bounds are invalid")

    selected_group = str(
        model.get("featureGroup")
        or selection.get("selectedFeatureGroup")
        or ""
    ).strip()
    if not selected_group or selected_group == "market_baseline":
        raise V8ModelRuntimeError("selected V8 model is not a learned candidate")
    training_steps = int(model.get("trainingSteps") or 0)
    if training_steps <= 0:
        raise V8ModelRuntimeError("selected V8 model has no training steps")

    payload = {
        "bundleVersion": BUNDLE_VERSION,
        "featureSchemaVersion": str(
            model.get("featureCompilerVersion")
            or model.get("featureSchemaVersion")
            or SCHEMA_VERSION
        ),
        "trainingReportDigest": str(
            training_report.get("resultDigest") or _digest(training_report)
        ),
        "sourceModelDigest": model.get("modelDigest"),
        "trainedAtUtc": training_report.get("createdAtUtc")
        or datetime.now(timezone.utc).isoformat(),
        "modelType": model_type,
        "featureNames": feature_names,
        "coefficients": weights,
        "standardizer": {"means": means, "scales": scales},
        "intercept": _finite(model.get("intercept"), "intercept"),
        "calibration": _calibration(model, selection),
        "probabilityBounds": [low, high],
        "marketProbabilityFeature": str(
            model.get("marketProbabilityFeature") or DEFAULT_MARKET_FEATURE
        ),
        "selectedFeatureGroup": selected_group,
        "trainingSteps": training_steps,
        "authority": "SHADOW_ONLY",
        "automaticWagerAllowed": False,
        "productionAuthorityChanged": False,
    }
    payload["modelDigest"] = _digest(payload)
    return payload


def verify_bundle(bundle: Mapping[str, Any]) -> None:
    required = {
        "bundleVersion",
        "featureSchemaVersion",
        "modelType",
        "featureNames",
        "coefficients",
        "standardizer",
        "intercept",
        "calibration",
        "probabilityBounds",
        "selectedFeatureGroup",
        "trainingSteps",
        "modelDigest",
    }
    missing = sorted(required.difference(bundle))
    if missing:
        raise V8ModelRuntimeError(
            f"V8 model bundle missing fields:{','.join(missing)}"
        )
    candidate = dict(bundle)
    digest = str(candidate.pop("modelDigest"))
    if _digest(candidate) != digest:
        raise V8ModelRuntimeError("V8 model bundle digest mismatch")
    if (
        bundle.get("authority") != "SHADOW_ONLY"
        or bundle.get("productionAuthorityChanged") is not False
        or bundle.get("automaticWagerAllowed") is not False
    ):
        raise V8ModelRuntimeError("V8 model bundle attempted to change authority")
    names = _sequence(bundle.get("featureNames"), "feature names")
    coefficients = _sequence(bundle.get("coefficients"), "coefficients")
    standardizer = bundle.get("standardizer") or {}
    if not isinstance(standardizer, Mapping):
        raise V8ModelRuntimeError("V8 standardizer is invalid")
    means = _sequence(standardizer.get("means"), "standardizer means")
    scales = _sequence(standardizer.get("scales"), "standardizer scales")
    if not (len(names) == len(coefficients) == len(means) == len(scales)):
        raise V8ModelRuntimeError("V8 runtime vector lengths do not match")
    for index, value in enumerate(coefficients):
        _finite(value, f"coefficient[{index}]")
    for index, value in enumerate(means):
        _finite(value, f"standardizer.mean[{index}]")
    for index, value in enumerate(scales):
        if abs(_finite(value, f"standardizer.scale[{index}]")) < 1e-12:
            raise V8ModelRuntimeError("V8 standardizer contains a zero scale")
    if str(bundle.get("selectedFeatureGroup") or "") == "market_baseline":
        raise V8ModelRuntimeError("market baseline cannot be a V8 model bundle")
    if int(bundle.get("trainingSteps") or 0) <= 0:
        raise V8ModelRuntimeError("V8 bundle has no training steps")
    if bundle.get("modelType") not in {RESIDUAL_MODEL, LEGACY_MODEL}:
        raise V8ModelRuntimeError("unsupported V8 model type")


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-_clip(value, -40.0, 40.0)))


def _logit(probability: float) -> float:
    probability = _clip(probability, 1e-9, 1.0 - 1e-9)
    return math.log(probability / (1.0 - probability))


def _calibrate(probability: float, calibration: Mapping[str, Any]) -> float:
    kind = str(calibration.get("type") or "identity").lower()
    if kind == "identity":
        return probability
    if kind in {"platt", "logistic"}:
        slope = _finite(
            calibration.get("slope", calibration.get("a", 1.0)),
            "calibration.slope",
        )
        intercept = _finite(
            calibration.get("intercept", calibration.get("b", 0.0)),
            "calibration.intercept",
        )
        return _sigmoid(slope * _logit(probability) + intercept)
    raise V8ModelRuntimeError(f"unsupported V8 calibration type:{kind}")


def _market_probability(
    bundle: Mapping[str, Any], feature_vector: Mapping[str, Any]
) -> float:
    requested = str(
        bundle.get("marketProbabilityFeature") or DEFAULT_MARKET_FEATURE
    )
    for name in (
        requested,
        "market_probability",
        "market_home_probability",
        "home_market_probability",
        "moneyline_home_probability",
    ):
        value = feature_vector.get(name)
        if value is None:
            continue
        parsed = _finite(value, name)
        if 0.0 < parsed < 1.0:
            return parsed
    raise V8ModelRuntimeError("live V8 market probability is missing or invalid")


def score(
    bundle: Mapping[str, Any], feature_vector: Mapping[str, Any]
) -> dict[str, Any]:
    verify_bundle(bundle)
    if not isinstance(feature_vector, Mapping):
        raise V8ModelRuntimeError("live V8 feature vector is not an object")
    missing = [
        name for name in bundle["featureNames"] if feature_vector.get(name) is None
    ]
    if missing:
        raise V8ModelRuntimeError(
            "live V8 feature vector incomplete:" + ",".join(missing)
        )
    means = bundle["standardizer"]["means"]
    scales = bundle["standardizer"]["scales"]
    residual = _finite(bundle["intercept"], "intercept")
    contributions: dict[str, float] = {}
    standardized: dict[str, float] = {}
    for name, weight, mean, scale in zip(
        bundle["featureNames"], bundle["coefficients"], means, scales
    ):
        transformed = (
            _finite(feature_vector[name], str(name)) - _finite(mean, "mean")
        ) / _finite(scale, "scale")
        contribution = _finite(weight, "weight") * transformed
        residual += contribution
        standardized[str(name)] = transformed
        contributions[str(name)] = contribution

    market_probability = None
    if bundle["modelType"] == RESIDUAL_MODEL:
        market_probability = _market_probability(bundle, feature_vector)
        raw = _sigmoid(_logit(market_probability) + residual)
    else:
        raw = _sigmoid(residual)
    low, high = [float(item) for item in bundle["probabilityBounds"]]
    raw = _clip(raw, low, high)
    probability = _clip(
        _calibrate(raw, bundle.get("calibration") or {}), low, high
    )
    return {
        "available": True,
        "authority": "SHADOW_ONLY",
        "automaticWagerAllowed": False,
        "probability": probability,
        "probabilityPct": round(probability * 100.0, 2),
        "pickSide": "home" if probability >= 0.5 else "away",
        "modelDigest": bundle["modelDigest"],
        "sourceModelDigest": bundle.get("sourceModelDigest"),
        "featureSchemaVersion": bundle["featureSchemaVersion"],
        "featureVectorDigest": _digest(
            {name: feature_vector[name] for name in bundle["featureNames"]}
        ),
        "marketProbability": market_probability,
        "marketOffsetUsed": bundle["modelType"] == RESIDUAL_MODEL,
        "standardizedFeatureValues": standardized,
        "featureContributions": contributions,
        "marketConsensusFallbackUsed": False,
        "productionAuthorityChanged": False,
    }
