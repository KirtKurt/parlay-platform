from __future__ import annotations

import json
import math
import os
from typing import Any, Dict, List

from mlb_ml_candidate_policy import VERSION as CANDIDATE_POLICY_VERSION, miss as candidate_profile_misses
from mlb_ml_feature_vector import VERSION as FEATURE_VECTOR_VERSION, feature_vector

VERSION = "MLB-ML-RUNTIME-OVERLAY-v5-probability-semantics-guarded-backstop"
MODEL_PATH = os.environ.get("INQSI_MLB_ML_MODEL_PATH", "runtime_reports/mlb_ml_model_latest.json")


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return default if value is None or value == "" else float(value)
    except Exception:
        return default


def _load_model() -> Dict[str, Any] | None:
    try:
        with open(MODEL_PATH, encoding="utf-8") as fh:
            model = json.load(fh)
        return model if isinstance(model, dict) and model.get("ok") else None
    except Exception:
        return None


def _score(row: Dict[str, Any], model: Dict[str, Any]):
    fmap = feature_vector(row)
    z = _f(model.get("bias"))
    weights = model.get("weights") or {}
    means = model.get("means") or {}
    scales = model.get("scales") or model.get("stds") or {}
    for name in model.get("features") or []:
        scale = _f(scales.get(name), 1.0) or 1.0
        z += _f(weights.get(name)) * ((_f(fmap.get(name)) - _f(means.get(name))) / scale)
    probability = 1.0 if z >= 35 else 0.0 if z <= -35 else 1.0 / (1.0 + math.exp(-z))
    return probability, fmap


def _threshold(model: Dict[str, Any] | None) -> Dict[str, Any]:
    model = model or {}
    return model.get("promotionThreshold") or model.get("guardedPromotionThreshold") or model.get("selectedThreshold") or {}


def _validated(model: Dict[str, Any] | None, info: Dict[str, Any], target: float) -> bool:
    if not model:
        return False
    if info.get("validated") is True and info.get("accuracyPct") is not None:
        return _f(info.get("accuracyPct")) >= target
    return bool(model.get("validatedAgainstTarget") is True or (info.get("accuracyPct") is not None and _f(info.get("accuracyPct")) >= target))


def _fatal(fmap: Dict[str, Any]) -> bool:
    return bool(
        _f(fmap.get("highReversalWeak")) >= 0.5
        or (_f(fmap.get("compressedMarket")) >= 0.5 and abs(_f(fmap.get("marketEdge"))) < 0.05)
        or _f(fmap.get("resistance")) >= 0.5
        or _f(fmap.get("favoriteRisk")) >= 0.5
        or _f(fmap.get("favoriteFlatMoveRisk")) >= 0.5
        or _f(fmap.get("favoriteCompressedRisk")) >= 0.5
        or _f(fmap.get("lowPullDepth")) >= 0.5
    )


def _profile(fmap: Dict[str, Any], profile: Any):
    if isinstance(profile, dict):
        misses = list(candidate_profile_misses(fmap, profile))
        if _f(fmap.get("reversalCount")) > 1:
            misses.append("reversalCount>1")
        if _fatal(fmap):
            misses.append("fatal_signal_profile")
        misses = sorted(set(misses))
        return not misses, str(profile.get("name") or "guarded_profile"), misses
    clean = (
        _f(fmap.get("marketEdge")) >= 0.08
        and _f(fmap.get("marketProb")) >= 0.54
        and _f(fmap.get("score")) >= 45
        and _f(fmap.get("reversalCount")) <= 1
        and _f(fmap.get("bookAgreement")) >= 0.5
        and not _fatal(fmap)
    )
    return clean, "clean_market_backstop" if clean else "no_backstop_profile", [] if clean else ["no_backstop_profile"]


def _signal_for_selected_side(row: Dict[str, Any]) -> Dict[str, Any]:
    return row.get("homeSignal") or {} if row.get("predictedSide") == "home" else row.get("awaySignal") or {}


def _true_team_probability(row: Dict[str, Any]) -> float | None:
    signal = _signal_for_selected_side(row)
    candidates = [
        signal.get("marketConsensusProbability"),
        row.get("marketConsensusProbability"),
        row.get("calibratedWinProbability"),
    ]
    for value in candidates:
        probability = _f(value, -1.0)
        if 0.0 < probability < 1.0:
            return probability
    return None


def _normalize_probability_fields(row: Dict[str, Any], reliability: float | None) -> None:
    original = row.get("winProbabilityPct")
    row["directionalSignalProbabilityPct"] = original
    true_probability = _true_team_probability(row)
    if true_probability is not None:
        if true_probability < 0.5:
            current_side = row.get("predictedSide")
            opposite_team = row.get("awayTeam") if current_side == "home" else row.get("homeTeam")
            if opposite_team:
                row["probabilityCorrectionApplied"] = True
                row["probabilityCorrectionReason"] = "selected_side_market_probability_below_50_flipped_to_opponent"
                row["predictedWinner"] = opposite_team
                row["predictedSide"] = "away" if current_side == "home" else "home"
                true_probability = 1.0 - true_probability
        pct = round(true_probability * 100.0, 2)
        row["teamWinProbabilityPct"] = pct
        row["winProbabilityPct"] = pct
        row["winProbabilityMeaning"] = "estimated_probability_selected_team_wins_game"
    else:
        row["teamWinProbabilityPct"] = None
        row["winProbabilityMeaning"] = "unavailable_not_ml_reliability"
    if reliability is not None:
        row["mlPickReliabilityPct"] = round(reliability * 100.0, 2)
        row["mlPickReliabilityMeaning"] = "estimated_probability_platform_selected_winner_is_correct"


def _promote(row: Dict[str, Any], tags: set[str], reason: str) -> None:
    tags.update({"ML_CONFIRMED", "ACTIONABLE_PICK"})
    tags.difference_update({"NO_PICK", "NO_PICK_DISCIPLINE", "NOT_PLAYABLE", "ML_REJECTED"})
    row.update({
        "officialPick": True,
        "accuracyTargetEligible": True,
        "actionablePick": True,
        "isOfficialDisplayPick": True,
        "displayGroup": "official",
        "recommendationStatus": "PLAYABLE_PREDICTION",
        "actionability": "ACTIONABLE_ML_CONFIRMED_WINNER",
        "actionabilityReason": reason,
    })


def _preserve(row: Dict[str, Any]) -> None:
    tags = set(row.get("tags") or [])
    has_winner = bool(row.get("predictedWinner"))
    playable = bool(row.get("actionablePick") is True or row.get("officialPick") is True)
    row.update({
        "predictionRequired": True,
        "requiredGameWinnerPrediction": has_winner,
        "winnerPredictionAvailable": has_winner,
        "displayPrediction": has_winner,
        "platformPick": has_winner,
        "customerVisibleWinnerPick": has_winner,
        "officialPrediction": has_winner,
        "predictionDisplayStatus": "REQUIRED_GAME_WINNER_PREDICTION" if has_winner else "MISSING_GAME_WINNER_PREDICTION",
        "recommendationStatus": "PLAYABLE_PREDICTION" if playable else "LOW_CONFIDENCE_PREDICTION_NOT_PLAYABLE",
    })
    if has_winner:
        tags.update({"REQUIRED_GAME_WINNER_PREDICTION", "PLATFORM_PICK"})
    if playable:
        tags.difference_update({"NO_PICK", "NO_PICK_DISCIPLINE", "NOT_PLAYABLE", "LOW_CONFIDENCE_PREDICTION"})
    else:
        tags.difference_update({"NO_PICK", "NO_PICK_DISCIPLINE"})
        tags.update({"LOW_CONFIDENCE_PREDICTION", "NOT_PLAYABLE"})
    row["tags"] = sorted(tags)


def _card(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "gameId": row.get("gameId"), "gameKey": row.get("gameKey"),
        "homeTeam": row.get("homeTeam"), "awayTeam": row.get("awayTeam"),
        "commenceTime": row.get("commenceTime"), "predictedWinner": row.get("predictedWinner"),
        "predictedSide": row.get("predictedSide"), "confidenceTier": row.get("confidenceTier"),
        "winProbabilityPct": row.get("winProbabilityPct"), "teamWinProbabilityPct": row.get("teamWinProbabilityPct"),
        "mlPickReliabilityPct": row.get("mlPickReliabilityPct"), "score": row.get("score"), "rank": row.get("rank"),
        "platformPick": row.get("platformPick"), "customerVisibleWinnerPick": row.get("customerVisibleWinnerPick"),
        "playable": bool(row.get("actionablePick") is True or row.get("officialPick") is True),
        "recommendationStatus": row.get("recommendationStatus"), "actionability": row.get("actionability"),
        "actionabilityReason": row.get("actionabilityReason"), "riskReasons": row.get("actionabilityRiskReasons") or [],
        "probabilityCorrectionApplied": row.get("probabilityCorrectionApplied", False), "tags": row.get("tags") or [],
    }


def enhance_result(result: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(result, dict) or not isinstance(result.get("predictions") or [], list):
        return result
    rows = result.get("predictions") or []
    enabled = os.environ.get("INQSI_MLB_ML_OVERLAY_ENABLED", "true").lower() in {"1", "true", "yes"}
    model = _load_model() if enabled else None
    info = _threshold(model)
    standard = (model or {}).get("selectedThreshold") or {}
    target = _f(os.environ.get("INQSI_MLB_ML_TARGET_ACCURACY"), 90.0)
    valid = _validated(model, info, target)
    threshold = _f(info.get("threshold"), _f(os.environ.get("INQSI_MLB_ML_MIN_PROBABILITY"), 0.70))
    reject_below = _f(os.environ.get("INQSI_MLB_ML_REJECT_BELOW"), 0.50)
    min_promotions = int(_f(os.environ.get("INQSI_MLB_ML_MIN_GUARDED_PROMOTIONS"), 2))
    max_promotions = int(_f(os.environ.get("INQSI_MLB_ML_MAX_GUARDED_PROMOTIONS"), 3))
    evaluated = promoted = rejected = 0
    candidates: List[Dict[str, Any]] = []

    for row in rows:
        overlay = {"enabled": enabled, "modelAvailable": bool(model), "applied": False, "validatedAgainstTarget": valid, "runtimeVersion": VERSION}
        probability = None
        if model:
            probability, fmap = _score(row, model)
            evaluated += 1
            profile_obj = info.get("profile") or info.get("guardPolicy")
            profile_ok, profile_name, misses = _profile(fmap, profile_obj if isinstance(profile_obj, dict) else None)
            primary_threshold = _f(standard.get("threshold"), threshold)
            confirmed = bool(valid and ((probability >= primary_threshold and not _fatal(fmap) and _f(fmap.get("reversalCount")) <= 1) or (probability >= threshold and profile_ok)))
            overlay.update({
                "applied": True, "probabilityPickCorrect": round(probability, 4), "confirmed": confirmed,
                "promotionThreshold": info, "standardThreshold": standard, "promotionProfile": profile_name,
                "promotionProfileMisses": misses, "promotionProfileOk": profile_ok,
                "candidatePolicyVersion": CANDIDATE_POLICY_VERSION,
                "modelVersion": model.get("version"), "featureVectorVersion": model.get("featureVectorVersion") or FEATURE_VECTOR_VERSION,
                "features": fmap,
            })
            tags = set(row.get("tags") or []) | {"ML_OVERLAY_EVALUATED"}
            if confirmed and promoted < max_promotions:
                _promote(row, tags, "validated_ml_overlay_confirmed")
                promoted += 1
            else:
                safe_backstop = bool(valid and probability >= 0.50 and not _fatal(fmap) and _f(fmap.get("reversalCount")) <= 1 and (_f(fmap.get("score")) >= 45 or _f(fmap.get("marketProb")) >= 0.54))
                if safe_backstop:
                    candidates.append({"row": row, "p": probability, "tags": tags})
                if probability < reject_below:
                    rejected += 1
                    row.update({"officialPick": False, "accuracyTargetEligible": False, "actionablePick": False,
                                "isOfficialDisplayPick": False, "actionability": "LOW_CONFIDENCE_ML_REJECTED_NOT_PLAYABLE",
                                "actionabilityReason": "ml_reliability_below_50_not_team_win_probability"})
                    row["actionabilityRiskReasons"] = sorted(set((row.get("actionabilityRiskReasons") or []) + ["ml_reliability_below_50"]))
                    tags.update({"ML_REJECTED", "LOW_CONFIDENCE_PREDICTION", "NOT_PLAYABLE"})
            row["tags"] = sorted(tags)
        _normalize_probability_fields(row, probability)
        row["mlOverlay"] = overlay

    if promoted < min_promotions:
        for item in sorted(candidates, key=lambda value: value["p"], reverse=True):
            if promoted >= min_promotions or promoted >= max_promotions:
                break
            row = item["row"]
            if row.get("actionablePick") is True:
                continue
            tags = set(row.get("tags") or []) | {"ML_GUARDED_BACKSTOP_PROMOTION"}
            _promote(row, tags, "validated_ml_top_safe_candidate_backstop")
            row["tags"] = sorted(tags)
            row["mlOverlay"].update({"confirmed": True, "confirmReason": "validated_ml_top_safe_candidate_backstop", "backstopPromotion": True})
            promoted += 1

    for row in rows:
        _preserve(row)
        row["finalPipelineVersion"] = VERSION
        row["finalGuardedStoreRequested"] = True

    actionable = sum(bool(row.get("actionablePick") is True or row.get("officialPick") is True) for row in rows)
    required = sum(bool(row.get("requiredGameWinnerPrediction")) for row in rows)
    displayed = sum(bool(row.get("displayPrediction")) for row in rows)
    low = sum(bool(row.get("displayPrediction")) and not (row.get("actionablePick") is True or row.get("officialPick") is True) for row in rows)
    summary = {
        "enabled": enabled, "modelAvailable": bool(model), "validatedAgainstTarget": valid,
        "evaluatedCount": evaluated, "promotedCount": promoted, "rejectedCount": rejected,
        "threshold": info, "standardThreshold": standard, "runtimeVersion": VERSION,
        "candidatePolicyVersion": CANDIDATE_POLICY_VERSION, "modelVersion": (model or {}).get("version"),
        "minGuardedPromotions": min_promotions, "maxGuardedPromotions": max_promotions,
        "probabilitySemanticsFixed": True,
        "teamWinProbabilityField": "teamWinProbabilityPct",
        "mlReliabilityField": "mlPickReliabilityPct",
        "requiredWinnerPickPreserved": True, "displayPredictionCount": displayed,
        "lowConfidencePredictionCount": low, "finalGuardedStoreRequested": True,
    }
    result.update({
        "actionablePickCount": actionable, "promotedCount": promoted, "lowConfidencePredictionCount": low,
        "nonActionablePredictionCount": low, "requiredGameWinnerPredictionCount": required,
        "displayPredictionCount": displayed, "allGamesHaveWinnerPrediction": bool(rows and required == len(rows)),
        "allGamesHaveDisplayedWinnerPrediction": bool(rows and displayed == len(rows)), "noPickCount": 0,
        "probabilitySemanticsFixed": True,
        "requiredWinnerPredictionDisplay": [_card(row) for row in rows if row.get("displayPrediction")],
    })
    result["officialPredictionDisplay"] = result["requiredWinnerPredictionDisplay"]
    result["nonOfficialPredictionDisplay"] = [_card(row) for row in rows if row.get("displayPrediction") and not (row.get("actionablePick") is True or row.get("officialPick") is True)]
    stack = result.get("winnerStackV2") or {}
    if isinstance(stack, dict):
        stack.update({"mlOverlay": summary, "actionablePickCount": actionable, "passNoPickCount": 0,
                      "lowConfidencePredictionCount": low, "requiredGameWinnerPredictionCount": required,
                      "displayPredictionCount": displayed, "allGamesHaveDisplayedWinnerPrediction": result["allGamesHaveDisplayedWinnerPrediction"]})
        result["winnerStackV2"] = stack
    target_summary = result.get("rolling24hAccuracyTarget") or result.get("accuracyTarget") or {}
    if isinstance(target_summary, dict):
        target_summary.update({"mlOverlay": summary, "actionablePickCount": actionable, "noPickCount": 0,
                               "lowConfidencePredictionCount": low, "requiredGameWinnerPredictionCount": required,
                               "displayPredictionCount": displayed, "allGamesHaveDisplayedWinnerPrediction": result["allGamesHaveDisplayedWinnerPrediction"]})
        result["rolling24hAccuracyTarget"] = target_summary
        result["accuracyTarget"] = target_summary
    if VERSION not in str(result.get("modelVersion") or ""):
        result["modelVersion"] = str(result.get("modelVersion") or "") + "+" + VERSION
    return result


def _store_final(module: Any, result: Dict[str, Any], requested: bool):
    if not requested or not isinstance(result, dict) or not hasattr(module, "_store_prediction"):
        return result
    count, errors = 0, []
    for row in result.get("predictions") or []:
        if not isinstance(row, dict):
            continue
        try:
            row["finalGuardedStored"] = True
            row["finalPipelineVersion"] = VERSION
            stored = module._store_prediction(row)
            row["finalGuardedStore"] = stored
            if isinstance(stored, dict) and stored.get("ok"):
                count += 1
            else:
                errors.append(str(stored))
        except Exception as exc:
            row["finalGuardedStored"] = False
            row["finalGuardedStoreError"] = str(exc)
            errors.append(str(exc))
    result.update({"finalGuardedStoredCount": count, "finalGuardedStoreErrors": errors, "finalGuardedStoreVersion": VERSION})
    return result


def apply(module: Any):
    if getattr(module, "_INQSI_MLB_ML_RUNTIME_OVERLAY_APPLIED", False):
        return module
    original = module.predict_all

    def patched_predict_all(*args, **kwargs):
        return _store_final(module, enhance_result(original(*args, **kwargs)), bool(kwargs.get("store")))

    module.predict_all = patched_predict_all
    module._INQSI_MLB_ML_RUNTIME_OVERLAY_APPLIED = True
    return module
