from __future__ import annotations

import json
import math
import os
from typing import Any, Dict, List

from mlb_ml_candidate_policy import VERSION as CANDIDATE_POLICY_VERSION, miss as candidate_profile_misses, ok as candidate_ok
from mlb_ml_feature_vector import VERSION as FEATURE_VECTOR_VERSION, feature_vector

VERSION = "MLB-ML-RUNTIME-OVERLAY-v3-required-winner-pick-display"
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
    return model.get("promotionThreshold") or model.get("guardedPromotionThreshold") or model.get("selectedThreshold") or {}


def _validated(model: Dict[str, Any] | None, threshold_info: Dict[str, Any], target: float) -> bool:
    if not model:
        return False
    if threshold_info.get("validated") is True and threshold_info.get("accuracyPct") is not None:
        return _f(threshold_info.get("accuracyPct"), 0.0) >= target
    if model.get("validatedAgainstTarget") is True:
        return True
    return bool(threshold_info.get("accuracyPct") is not None and _f(threshold_info.get("accuracyPct"), 0.0) >= target)


def _fatal(features: Dict[str, float]) -> bool:
    return bool(
        _f(features.get("highReversalWeak")) >= 0.5
        or (_f(features.get("compressedMarket")) >= 0.5 and abs(_f(features.get("marketEdge"))) < 0.05)
        or _f(features.get("passTier")) >= 0.5
        or _f(features.get("resistance")) >= 0.5
        or _f(features.get("favoriteRisk")) >= 0.5
        or _f(features.get("favoriteFlatMoveRisk")) >= 0.5
        or _f(features.get("favoriteCompressedRisk")) >= 0.5
    )


def _fallback_profile_ok(features: Dict[str, float]) -> tuple[bool, str, List[str]]:
    if _fatal(features):
        return False, "fatal_signal_profile", ["fatal_signal_profile"]
    if _f(features.get("marketEdge")) >= 0.08 and _f(features.get("marketProb")) >= 0.54 and _f(features.get("score")) >= 45:
        return True, "clean_market_backstop", []
    if _f(features.get("selectedUnderdog")) >= 0.5 and _f(features.get("underdogPositiveMove")) >= 0.5 and _f(features.get("marketEdge")) >= -0.06:
        return True, "underdog_positive_move_backstop", []
    return False, "no_backstop_profile", ["no_backstop_profile"]


def _profile_status(features: Dict[str, float], profile: Dict[str, Any] | None) -> tuple[bool, str, List[str]]:
    if isinstance(profile, dict):
        misses = candidate_profile_misses(features, profile)
        return not misses, str(profile.get("name") or "guarded_profile"), misses
    return _fallback_profile_ok(features)


def _promote(row: Dict[str, Any], tags: set[str], reason: str) -> None:
    tags.add("ML_CONFIRMED")
    tags.add("ACTIONABLE_PICK")
    tags.discard("NO_PICK")
    tags.discard("NO_PICK_DISCIPLINE")
    row["officialPick"] = True
    row["accuracyTargetEligible"] = True
    row["actionablePick"] = True
    row["actionability"] = "ACTIONABLE_ML_CONFIRMED_WINNER"
    row["actionabilityReason"] = reason


def _preserve_required_winner_pick(row: Dict[str, Any]) -> None:
    """Keep the required winner prediction visible even when not playable.

    Actionability is a bet-quality gate. It must never erase the platform's
    required one-winner-per-game prediction.
    """
    tags = set(row.get("tags") or [])
    has_winner = bool(row.get("predictedWinner"))
    row["predictionRequired"] = True
    row["requiredGameWinnerPrediction"] = has_winner
    row["winnerPredictionAvailable"] = has_winner
    row["displayPrediction"] = has_winner
    row["platformPick"] = has_winner
    row["customerVisibleWinnerPick"] = has_winner
    row["officialPrediction"] = has_winner
    row["predictionDisplayStatus"] = "REQUIRED_GAME_WINNER_PREDICTION" if has_winner else "MISSING_GAME_WINNER_PREDICTION"
    if has_winner:
        tags.add("REQUIRED_GAME_WINNER_PREDICTION")
        tags.add("PLATFORM_PICK")
    if row.get("actionablePick") is True or row.get("officialPick") is True:
        row["recommendationStatus"] = "PLAYABLE_PREDICTION"
        tags.discard("NO_PICK")
        tags.discard("NO_PICK_DISCIPLINE")
    else:
        row["recommendationStatus"] = "LOW_CONFIDENCE_PREDICTION_NOT_PLAYABLE"
        tags.discard("NO_PICK")
        tags.discard("NO_PICK_DISCIPLINE")
        tags.add("LOW_CONFIDENCE_PREDICTION")
        tags.add("NOT_PLAYABLE")
    row["tags"] = sorted(tags)


def _display_card(row: Dict[str, Any]) -> Dict[str, Any]:
    playable = bool(row.get("actionablePick") is True or row.get("officialPick") is True)
    return {
        "gameId": row.get("gameId"),
        "gameKey": row.get("gameKey"),
        "homeTeam": row.get("homeTeam"),
        "awayTeam": row.get("awayTeam"),
        "commenceTime": row.get("commenceTime"),
        "predictedWinner": row.get("predictedWinner"),
        "predictedSide": row.get("predictedSide"),
        "confidenceTier": row.get("confidenceTier"),
        "winProbabilityPct": row.get("winProbabilityPct"),
        "score": row.get("score"),
        "rank": row.get("rank"),
        "platformPick": row.get("platformPick"),
        "customerVisibleWinnerPick": row.get("customerVisibleWinnerPick"),
        "playable": playable,
        "recommendationStatus": row.get("recommendationStatus"),
        "actionability": row.get("actionability"),
        "actionabilityReason": row.get("actionabilityReason"),
        "riskReasons": row.get("actionabilityRiskReasons") or [],
        "tags": row.get("tags") or [],
    }


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
    guarded_threshold_info = (model or {}).get("promotionThreshold") or (model or {}).get("guardedPromotionThreshold")
    target = _f(os.environ.get("INQSI_MLB_ML_TARGET_ACCURACY"), 90.0)
    validated = _validated(model, threshold_info, target)
    threshold = _f(threshold_info.get("threshold"), _f(os.environ.get("INQSI_MLB_ML_MIN_PROBABILITY"), 0.90))
    reject_below = _f(os.environ.get("INQSI_MLB_ML_REJECT_BELOW"), 0.52)
    min_promotions = int(_f(os.environ.get("INQSI_MLB_ML_MIN_GUARDED_PROMOTIONS"), 1.0))
    max_promotions = int(_f(os.environ.get("INQSI_MLB_ML_MAX_GUARDED_PROMOTIONS"), 3.0))
    evaluated = promoted = rejected = 0
    backstop_candidates: List[Dict[str, Any]] = []

    for row in rows:
        overlay = {
            "enabled": enabled,
            "modelAvailable": bool(model),
            "applied": False,
            "validatedAgainstTarget": validated,
            "runtimeVersion": VERSION,
            "candidatePolicyVersion": CANDIDATE_POLICY_VERSION,
            "modelVersion": (model or {}).get("version"),
            "featureVectorVersion": (model or {}).get("featureVectorVersion") or FEATURE_VECTOR_VERSION,
            "rowCount": (model or {}).get("rowCount"),
            "trainingSource": (model or {}).get("trainingSource"),
        }
        if model:
            p, fmap = _score(row, model)
            evaluated += 1
            profile = threshold_info.get("profile") or threshold_info.get("guardPolicy")
            profile_ok, profile_name, profile_misses = _profile_status(fmap, profile if isinstance(profile, dict) else None)
            primary_confirmed = bool(p is not None and validated and p >= _f(standard_threshold_info.get("threshold"), threshold) and not _fatal(fmap))
            guarded_confirmed = bool(p is not None and validated and p >= threshold and profile_ok)
            confirmed = primary_confirmed or guarded_confirmed
            reason = "validated_ml_overlay_primary_threshold" if primary_confirmed else "validated_ml_overlay_guarded_lower_threshold" if guarded_confirmed else None
            overlay.update({
                "applied": True,
                "probabilityPickCorrect": round(p, 4) if p is not None else None,
                "confirmed": confirmed,
                "confirmReason": reason,
                "promotionThreshold": threshold_info,
                "standardThreshold": standard_threshold_info,
                "guardedThreshold": guarded_threshold_info,
                "promotionProfile": profile_name,
                "promotionProfileMisses": profile_misses,
                "promotionProfileOk": profile_ok,
                "usesGuardedLowerThreshold": bool(isinstance(profile, dict)),
                "features": fmap,
            })
            tags = set(row.get("tags") or [])
            tags.add("ML_OVERLAY_EVALUATED")
            if p is not None and p < reject_below:
                row["officialPick"] = False
                row["accuracyTargetEligible"] = False
                row["actionablePick"] = False
                row["actionability"] = "LOW_CONFIDENCE_ML_REJECTED_NOT_PLAYABLE"
                row["actionabilityReason"] = "ml_overlay_rejected_low_correct_probability_but_required_winner_pick_preserved"
                risks = list(row.get("actionabilityRiskReasons") or [])
                risks.append("ml_overlay_rejected_low_correct_probability")
                row["actionabilityRiskReasons"] = sorted(set(risks))
                tags.add("ML_REJECTED")
                tags.add("LOW_CONFIDENCE_PREDICTION")
                tags.add("NOT_PLAYABLE")
                rejected += 1
            if confirmed and promoted < max_promotions:
                tags.add("ML_GUARDED_PROMOTION" if isinstance(profile, dict) else "ML_STANDARD_PROMOTION")
                _promote(row, tags, reason or "validated_ml_overlay_confirms_platform_selected_winner")
                promoted += 1
            elif p is not None and validated and profile_ok and p >= max(0.50, min(threshold, _f(standard_threshold_info.get("threshold"), threshold))):
                backstop_candidates.append({"row": row, "tags": tags, "p": p, "profile": profile_name, "reason": "validated_ml_overlay_top_guarded_candidate"})
            row["tags"] = sorted(tags)
        row["mlOverlay"] = overlay

    if promoted < min_promotions and model and validated:
        for item in sorted(backstop_candidates, key=lambda x: x.get("p") or 0.0, reverse=True):
            if promoted >= min_promotions or promoted >= max_promotions:
                break
            row = item["row"]
            if row.get("actionablePick") is True:
                continue
            tags = set(row.get("tags") or [])
            tags.add("ML_GUARDED_BACKSTOP_PROMOTION")
            _promote(row, tags, item.get("reason") or "validated_ml_overlay_top_guarded_candidate")
            row["tags"] = sorted(tags)
            overlay = row.get("mlOverlay") or {}
            overlay["confirmed"] = True
            overlay["confirmReason"] = item.get("reason")
            overlay["backstopPromotion"] = True
            overlay["promotionProfile"] = item.get("profile")
            row["mlOverlay"] = overlay
            promoted += 1

    for row in rows:
        _preserve_required_winner_pick(row)

    actionable_count = len([r for r in rows if r.get("actionablePick") is True or r.get("officialPick") is True])
    required_count = len([r for r in rows if r.get("requiredGameWinnerPrediction") is True])
    display_count = len([r for r in rows if r.get("displayPrediction") is True])
    missing_display = [r.get("gameKey") or r.get("gameId") for r in rows if not r.get("displayPrediction")]
    low_confidence_count = len([r for r in rows if r.get("displayPrediction") is True and not (r.get("actionablePick") is True or r.get("officialPick") is True)])
    result["actionablePickCount"] = actionable_count
    result["lowConfidencePredictionCount"] = low_confidence_count
    result["nonActionablePredictionCount"] = low_confidence_count
    result["requiredGameWinnerPredictionCount"] = required_count
    result["displayPredictionCount"] = display_count
    result["missingDisplayedWinnerPredictions"] = missing_display
    result["allGamesHaveWinnerPrediction"] = bool(rows and required_count == len(rows))
    result["allGamesHaveDisplayedWinnerPrediction"] = bool(rows and display_count == len(rows))
    # Preserve old key without using it as customer-facing product language.
    result["noPickCount"] = 0
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
        "candidatePolicyVersion": CANDIDATE_POLICY_VERSION,
        "modelVersion": (model or {}).get("version"),
        "featureVectorVersion": (model or {}).get("featureVectorVersion") or FEATURE_VECTOR_VERSION,
        "rowCount": (model or {}).get("rowCount"),
        "trainingSource": (model or {}).get("trainingSource"),
        "minGuardedPromotions": min_promotions,
        "maxGuardedPromotions": max_promotions,
        "requiredWinnerPickPreserved": True,
        "displayPredictionCount": display_count,
        "lowConfidencePredictionCount": low_confidence_count,
    }
    result["requiredWinnerPredictionDisplay"] = [_display_card(r) for r in rows if r.get("displayPrediction")]
    result["officialPredictionDisplay"] = result["requiredWinnerPredictionDisplay"]
    result["nonOfficialPredictionDisplay"] = [_display_card(r) for r in rows if r.get("displayPrediction") and not (r.get("actionablePick") is True or r.get("officialPick") is True)]
    stack = result.get("winnerStackV2") or {}
    if isinstance(stack, dict):
        stack["mlOverlay"] = overlay_summary
        stack["actionablePickCount"] = result["actionablePickCount"]
        stack["passNoPickCount"] = 0
        stack["lowConfidencePredictionCount"] = result["lowConfidencePredictionCount"]
        stack["requiredGameWinnerPredictionCount"] = result["requiredGameWinnerPredictionCount"]
        stack["displayPredictionCount"] = result["displayPredictionCount"]
        stack["allGamesHaveDisplayedWinnerPrediction"] = result["allGamesHaveDisplayedWinnerPrediction"]
        result["winnerStackV2"] = stack
    target_summary = result.get("rolling24hAccuracyTarget") or result.get("accuracyTarget") or {}
    if isinstance(target_summary, dict):
        target_summary["mlOverlay"] = overlay_summary
        target_summary["actionablePickCount"] = result["actionablePickCount"]
        target_summary["noPickCount"] = 0
        target_summary["lowConfidencePredictionCount"] = result["lowConfidencePredictionCount"]
        target_summary["requiredGameWinnerPredictionCount"] = result["requiredGameWinnerPredictionCount"]
        target_summary["displayPredictionCount"] = result["displayPredictionCount"]
        target_summary["allGamesHaveDisplayedWinnerPrediction"] = result["allGamesHaveDisplayedWinnerPrediction"]
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
