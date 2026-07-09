from __future__ import annotations

import json
import math
import os
from typing import Any, Dict

from mlb_ml_candidate_policy import miss as candidate_profile_misses
from mlb_ml_feature_vector import VERSION as FEATURE_VECTOR_VERSION, feature_vector

VERSION = "MLB-ML-RUNTIME-OVERLAY-v1.1-guarded-lower-threshold"
MODEL_PATH = os.environ.get("INQSI_MLB_ML_MODEL_PATH", "runtime_reports/mlb_ml_model_latest.json")


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _load_model() -> Dict[str, Any] | None:
    try:
        with open(MODEL_PATH, "r", encoding="utf-8") as f:
            model = json.load(f)
        return model if isinstance(model, dict) and model.get("ok") else None
    except Exception:
        return None


def _score(row: Dict[str, Any], model: Dict[str, Any]) -> tuple[float | None, Dict[str, float]]:
    features = model.get("features") or []
    weights = model.get("weights") or {}
    means = model.get("means") or {}
    scales = model.get("scales") or model.get("stds") or {}
    fmap = feature_vector(row)
    z = _f(model.get("bias"), 0.0)
    for feature in features:
        scale = _f(scales.get(feature), 1.0) or 1.0
        z += _f(weights.get(feature), 0.0) * ((_f(fmap.get(feature), 0.0) - _f(means.get(feature), 0.0)) / scale)
    if z >= 35:
        return 1.0, fmap
    if z <= -35:
        return 0.0, fmap
    return 1.0 / (1.0 + math.exp(-z)), fmap


def _threshold_info(model: Dict[str, Any] | None) -> Dict[str, Any]:
    if not model:
        return {}
    return (
        model.get("promotionThreshold")
        or model.get("guardedPromotionThreshold")
        or model.get("selectedThreshold")
        or {}
    )


def _validated(model: Dict[str, Any] | None, threshold_info: Dict[str, Any], target: float) -> bool:
    if not model:
        return False
    if threshold_info.get("validated") is True and threshold_info.get("accuracyPct") is not None:
        return _f(threshold_info.get("accuracyPct"), 0.0) >= target
    if model.get("validatedAgainstTarget") is True:
        return True
    return bool(threshold_info.get("accuracyPct") is not None and _f(threshold_info.get("accuracyPct"), 0.0) >= target)


def enhance_result(result: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return result
    rows = result.get("predictions") or []
    if not isinstance(rows, list):
        return result
    enabled = os.environ.get("INQSI_MLB_ML_OVERLAY_ENABLED", "true").lower() in {"1", "true", "yes"}
    model = _load_model() if enabled else None
    threshold_info = _threshold_info(model)
    standard_threshold_info = (model or {}).get("selectedThreshold") or {}
    guarded_threshold_info = (model or {}).get("guardedPromotionThreshold")
    target = _f(os.environ.get("INQSI_MLB_ML_TARGET_ACCURACY"), 90.0)
    validated = _validated(model, threshold_info, target)
    threshold = _f(threshold_info.get("threshold"), _f(os.environ.get("INQSI_MLB_ML_MIN_PROBABILITY"), 0.90))
    reject_below = _f(os.environ.get("INQSI_MLB_ML_REJECT_BELOW"), 0.52)
    evaluated = promoted = rejected = 0

    for row in rows:
        overlay = {
            "enabled": enabled,
            "modelAvailable": bool(model),
            "applied": False,
            "validatedAgainstTarget": validated,
            "runtimeVersion": VERSION,
            "modelVersion": (model or {}).get("version"),
            "featureVectorVersion": (model or {}).get("featureVectorVersion") or FEATURE_VECTOR_VERSION,
            "rowCount": (model or {}).get("rowCount"),
            "trainingSource": (model or {}).get("trainingSource"),
        }
        if model:
            p, fmap = _score(row, model)
            evaluated += 1
            profile = threshold_info.get("profile") or threshold_info.get("guardPolicy")
            profile_misses = candidate_profile_misses(fmap, profile) if isinstance(profile, dict) else []
            profile_ok = not profile_misses
            risk_reasons = row.get("actionabilityRiskReasons") or []
            if isinstance(profile, dict):
                confirmed = bool(p is not None and validated and p >= threshold and profile_ok)
            else:
                confirmed = bool(p is not None and validated and p >= threshold and not risk_reasons)
            overlay.update({
                "applied": True,
                "probabilityPickCorrect": round(p, 4) if p is not None else None,
                "confirmed": confirmed,
                "promotionThreshold": threshold_info,
                "standardThreshold": standard_threshold_info,
                "guardedThreshold": guarded_threshold_info,
                "promotionProfileMisses": profile_misses,
                "usesGuardedLowerThreshold": bool(isinstance(profile, dict)),
            })
            tags = set(row.get("tags") or [])
            tags.add("ML_OVERLAY_EVALUATED")
            if p is not None and p < reject_below:
                row["officialPick"] = False
                row["accuracyTargetEligible"] = False
                row["actionablePick"] = False
                row["actionability"] = "NO_PICK_ML_REJECTED"
                row["actionabilityReason"] = "ml_overlay_rejected_low_correct_probability"
                risks = list(row.get("actionabilityRiskReasons") or [])
                risks.append("ml_overlay_rejected_low_correct_probability")
                row["actionabilityRiskReasons"] = sorted(set(risks))
                tags.add("ML_REJECTED")
                rejected += 1
            if confirmed:
                tags.add("ML_CONFIRMED")
                tags.add("ML_GUARDED_PROMOTION" if isinstance(profile, dict) else "ML_STANDARD_PROMOTION")
                row["officialPick"] = True
                row["accuracyTargetEligible"] = True
                row["actionablePick"] = True
                row["actionability"] = "ACTIONABLE_ML_CONFIRMED_WINNER"
                row["actionabilityReason"] = "validated_ml_overlay_confirms_platform_selected_winner_with_guarded_threshold"
                promoted += 1
            row["tags"] = sorted(tags)
        row["mlOverlay"] = overlay

    result["actionablePickCount"] = len([r for r in rows if r.get("actionablePick")])
    result["noPickCount"] = len([r for r in rows if not r.get("actionablePick")])
    overlay_summary = {
        "enabled": enabled,
        "modelAvailable": bool(model),
        "validatedAgainstTarget": validated,
        "evaluatedCount": evaluated,
        "promotedCount": promoted,
        "rejectedCount": rejected,
        "threshold": threshold_info,
        "standardThreshold": standard_threshold_info,
        "guardedThreshold": guarded_threshold_info,
        "runtimeVersion": VERSION,
        "modelVersion": (model or {}).get("version"),
        "featureVectorVersion": (model or {}).get("featureVectorVersion") or FEATURE_VECTOR_VERSION,
        "rowCount": (model or {}).get("rowCount"),
        "trainingSource": (model or {}).get("trainingSource"),
    }
    stack = result.get("winnerStackV2") or {}
    if isinstance(stack, dict):
        stack["mlOverlay"] = overlay_summary
        stack["actionablePickCount"] = result["actionablePickCount"]
        stack["passNoPickCount"] = result["noPickCount"]
        result["winnerStackV2"] = stack
    target_summary = result.get("rolling24hAccuracyTarget") or result.get("accuracyTarget") or {}
    if isinstance(target_summary, dict):
        target_summary["mlOverlay"] = overlay_summary
        target_summary["actionablePickCount"] = result["actionablePickCount"]
        target_summary["noPickCount"] = result["noPickCount"]
        result["rolling24hAccuracyTarget"] = target_summary
        result["accuracyTarget"] = target_summary
    if VERSION not in str(result.get("modelVersion") or ""):
        result["modelVersion"] = str(result.get("modelVersion") or "") + "+" + VERSION
    return result


def apply(module: Any):
    if getattr(module, "_INQSI_MLB_ML_RUNTIME_OVERLAY_APPLIED", False):
        return module
    original = module.predict_all

    def patched_predict_all(*args, **kwargs):
        return enhance_result(original(*args, **kwargs))

    module.predict_all = patched_predict_all
    module._INQSI_MLB_ML_RUNTIME_OVERLAY_APPLIED = True
    return module
