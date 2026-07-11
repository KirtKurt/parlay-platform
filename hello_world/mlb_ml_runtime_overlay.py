from __future__ import annotations

import json
import math
import os
from typing import Any, Dict, List

from mlb_ml_candidate_policy import VERSION as CANDIDATE_POLICY_VERSION, miss as candidate_profile_misses
from mlb_ml_feature_vector import VERSION as FEATURE_VECTOR_VERSION, feature_vector

VERSION = "MLB-ML-RUNTIME-OVERLAY-v4-final-guarded-store"
MODEL_PATH = os.environ.get("INQSI_MLB_ML_MODEL_PATH", "runtime_reports/mlb_ml_model_latest.json")


def _f(v: Any, d: float = 0.0) -> float:
    try:
        return d if v is None or v == "" else float(v)
    except Exception:
        return d


def _load_model() -> Dict[str, Any] | None:
    try:
        model = json.load(open(MODEL_PATH, encoding="utf-8"))
        return model if isinstance(model, dict) and model.get("ok") else None
    except Exception:
        return None


def _score(row: Dict[str, Any], model: Dict[str, Any]):
    fmap = feature_vector(row)
    z = _f(model.get("bias"))
    weights, means = model.get("weights") or {}, model.get("means") or {}
    scales = model.get("scales") or model.get("stds") or {}
    for name in model.get("features") or []:
        scale = _f(scales.get(name), 1.0) or 1.0
        z += _f(weights.get(name)) * ((_f(fmap.get(name)) - _f(means.get(name))) / scale)
    p = 1.0 if z >= 35 else 0.0 if z <= -35 else 1.0 / (1.0 + math.exp(-z))
    return p, fmap


def _threshold(model):
    return (model or {}).get("promotionThreshold") or (model or {}).get("guardedPromotionThreshold") or (model or {}).get("selectedThreshold") or {}


def _validated(model, info, target):
    if not model:
        return False
    if info.get("validated") is True and info.get("accuracyPct") is not None:
        return _f(info.get("accuracyPct")) >= target
    return bool(model.get("validatedAgainstTarget") is True or (info.get("accuracyPct") is not None and _f(info.get("accuracyPct")) >= target))


def _fatal(fmap):
    return bool(
        _f(fmap.get("highReversalWeak")) >= .5
        or (_f(fmap.get("compressedMarket")) >= .5 and abs(_f(fmap.get("marketEdge"))) < .05)
        or _f(fmap.get("passTier")) >= .5
        or _f(fmap.get("resistance")) >= .5
        or _f(fmap.get("favoriteRisk")) >= .5
        or _f(fmap.get("favoriteFlatMoveRisk")) >= .5
        or _f(fmap.get("favoriteCompressedRisk")) >= .5
        or _f(fmap.get("lowPullDepth")) >= .5
    )


def _profile(fmap, profile):
    if isinstance(profile, dict):
        misses = list(candidate_profile_misses(fmap, profile))
        if _f(fmap.get("reversalCount")) > 1:
            misses.append("reversalCount>1")
        if _fatal(fmap):
            misses.append("fatal_signal_profile")
        misses = sorted(set(misses))
        return not misses, str(profile.get("name") or "guarded_profile"), misses
    misses = []
    if _fatal(fmap):
        misses.append("fatal_signal_profile")
    clean = (
        _f(fmap.get("marketEdge")) >= .12
        and _f(fmap.get("marketProb")) >= .56
        and _f(fmap.get("score")) >= 55
        and _f(fmap.get("reversalCount")) <= 1
        and _f(fmap.get("bookAgreement")) >= .5
    )
    dog = (
        _f(fmap.get("selectedUnderdog")) >= .5
        and _f(fmap.get("underdogPositiveMove")) >= .5
        and _f(fmap.get("marketEdge")) >= -.04
        and _f(fmap.get("reversalCount")) <= 1
    )
    if not (clean or dog):
        misses.append("no_backstop_profile")
    return not misses, "clean_market_backstop" if clean else "underdog_positive_move_backstop" if dog else "no_backstop_profile", misses


def _promote(row, tags, reason):
    tags.update({"ML_CONFIRMED", "ACTIONABLE_PICK"})
    tags.difference_update({"NO_PICK", "NO_PICK_DISCIPLINE", "NOT_PLAYABLE"})
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


def _preserve(row):
    tags = set(row.get("tags") or [])
    has_winner = bool(row.get("predictedWinner"))
    row.update({
        "predictionRequired": True,
        "requiredGameWinnerPrediction": has_winner,
        "winnerPredictionAvailable": has_winner,
        "displayPrediction": has_winner,
        "platformPick": has_winner,
        "customerVisibleWinnerPick": has_winner,
        "officialPrediction": has_winner,
        "predictionDisplayStatus": "REQUIRED_GAME_WINNER_PREDICTION" if has_winner else "MISSING_GAME_WINNER_PREDICTION",
    })
    if has_winner:
        tags.update({"REQUIRED_GAME_WINNER_PREDICTION", "PLATFORM_PICK"})
    playable = bool(row.get("actionablePick") is True or row.get("officialPick") is True)
    if playable:
        row["recommendationStatus"] = "PLAYABLE_PREDICTION"
        tags.difference_update({"NO_PICK", "NO_PICK_DISCIPLINE", "NOT_PLAYABLE"})
    else:
        row["recommendationStatus"] = "LOW_CONFIDENCE_PREDICTION_NOT_PLAYABLE"
        tags.difference_update({"NO_PICK", "NO_PICK_DISCIPLINE"})
        tags.update({"LOW_CONFIDENCE_PREDICTION", "NOT_PLAYABLE"})
    row["tags"] = sorted(tags)


def _card(row):
    return {
        "gameId": row.get("gameId"), "gameKey": row.get("gameKey"),
        "homeTeam": row.get("homeTeam"), "awayTeam": row.get("awayTeam"),
        "commenceTime": row.get("commenceTime"), "predictedWinner": row.get("predictedWinner"),
        "predictedSide": row.get("predictedSide"), "confidenceTier": row.get("confidenceTier"),
        "winProbabilityPct": row.get("winProbabilityPct"), "score": row.get("score"), "rank": row.get("rank"),
        "platformPick": row.get("platformPick"), "customerVisibleWinnerPick": row.get("customerVisibleWinnerPick"),
        "playable": bool(row.get("actionablePick") is True or row.get("officialPick") is True),
        "recommendationStatus": row.get("recommendationStatus"), "actionability": row.get("actionability"),
        "actionabilityReason": row.get("actionabilityReason"), "riskReasons": row.get("actionabilityRiskReasons") or [],
        "tags": row.get("tags") or [],
    }


def enhance_result(result: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(result, dict) or not isinstance(result.get("predictions") or [], list):
        return result
    rows = result.get("predictions") or []
    enabled = os.environ.get("INQSI_MLB_ML_OVERLAY_ENABLED", "true").lower() in {"1", "true", "yes"}
    model = _load_model() if enabled else None
    info = _threshold(model)
    standard = (model or {}).get("selectedThreshold") or {}
    guarded = (model or {}).get("promotionThreshold") or (model or {}).get("guardedPromotionThreshold")
    target = _f(os.environ.get("INQSI_MLB_ML_TARGET_ACCURACY"), 90.0)
    valid = _validated(model, info, target)
    threshold = _f(info.get("threshold"), _f(os.environ.get("INQSI_MLB_ML_MIN_PROBABILITY"), .90))
    reject_below = _f(os.environ.get("INQSI_MLB_ML_REJECT_BELOW"), .52)
    min_promotions = int(_f(os.environ.get("INQSI_MLB_ML_MIN_GUARDED_PROMOTIONS"), 0))
    max_promotions = int(_f(os.environ.get("INQSI_MLB_ML_MAX_GUARDED_PROMOTIONS"), 3))
    evaluated = promoted = rejected = 0
    backstops: List[Dict[str, Any]] = []

    for row in rows:
        overlay = {
            "enabled": enabled, "modelAvailable": bool(model), "applied": False,
            "validatedAgainstTarget": valid, "runtimeVersion": VERSION,
            "candidatePolicyVersion": CANDIDATE_POLICY_VERSION, "modelVersion": (model or {}).get("version"),
            "featureVectorVersion": (model or {}).get("featureVectorVersion") or FEATURE_VECTOR_VERSION,
            "rowCount": (model or {}).get("rowCount"), "trainingSource": (model or {}).get("trainingSource"),
        }
        if model:
            p, fmap = _score(row, model)
            evaluated += 1
            profile_obj = info.get("profile") or info.get("guardPolicy")
            profile_ok, profile_name, misses = _profile(fmap, profile_obj if isinstance(profile_obj, dict) else None)
            primary = bool(valid and p >= _f(standard.get("threshold"), threshold) and not _fatal(fmap) and _f(fmap.get("reversalCount")) <= 1)
            guarded_ok = bool(valid and p >= threshold and profile_ok)
            confirmed = primary or guarded_ok
            reason = "validated_ml_overlay_primary_threshold_clean_profile" if primary else "validated_ml_overlay_guarded_lower_threshold_clean_profile" if guarded_ok else None
            overlay.update({
                "applied": True, "probabilityPickCorrect": round(p, 4), "confirmed": confirmed,
                "confirmReason": reason, "promotionThreshold": info, "standardThreshold": standard,
                "guardedThreshold": guarded, "promotionProfile": profile_name,
                "promotionProfileMisses": misses, "promotionProfileOk": profile_ok,
                "usesGuardedLowerThreshold": isinstance(profile_obj, dict), "features": fmap,
            })
            tags = set(row.get("tags") or []) | {"ML_OVERLAY_EVALUATED"}
            if p < reject_below:
                row.update({
                    "officialPick": False, "accuracyTargetEligible": False, "actionablePick": False,
                    "isOfficialDisplayPick": False, "actionability": "LOW_CONFIDENCE_ML_REJECTED_NOT_PLAYABLE",
                    "actionabilityReason": "ml_overlay_rejected_low_correct_probability_but_required_winner_pick_preserved",
                })
                row["actionabilityRiskReasons"] = sorted(set((row.get("actionabilityRiskReasons") or []) + ["ml_overlay_rejected_low_correct_probability"]))
                tags.update({"ML_REJECTED", "LOW_CONFIDENCE_PREDICTION", "NOT_PLAYABLE"})
                rejected += 1
            if confirmed and promoted < max_promotions:
                tags.add("ML_GUARDED_PROMOTION" if isinstance(profile_obj, dict) else "ML_STANDARD_PROMOTION")
                _promote(row, tags, reason or "validated_ml_overlay_confirms_platform_selected_winner")
                promoted += 1
            elif min_promotions > 0 and valid and profile_ok and p >= max(.50, min(threshold, _f(standard.get("threshold"), threshold))):
                backstops.append({"row": row, "p": p, "profile": profile_name})
            row["tags"] = sorted(tags)
        row["mlOverlay"] = overlay

    if min_promotions > 0 and promoted < min_promotions and model and valid:
        for item in sorted(backstops, key=lambda x: x["p"], reverse=True):
            if promoted >= min_promotions or promoted >= max_promotions:
                break
            row = item["row"]
            if row.get("actionablePick") is True:
                continue
            tags = set(row.get("tags") or []) | {"ML_GUARDED_BACKSTOP_PROMOTION"}
            _promote(row, tags, "validated_ml_overlay_top_guarded_candidate")
            row["tags"] = sorted(tags)
            row["mlOverlay"].update({"confirmed": True, "confirmReason": "validated_ml_overlay_top_guarded_candidate", "backstopPromotion": True})
            promoted += 1

    for row in rows:
        _preserve(row)
        row["finalPipelineVersion"] = VERSION
        row["finalGuardedStoreRequested"] = True

    actionable = sum(bool(r.get("actionablePick") is True or r.get("officialPick") is True) for r in rows)
    required = sum(bool(r.get("requiredGameWinnerPrediction")) for r in rows)
    displayed = sum(bool(r.get("displayPrediction")) for r in rows)
    low = sum(bool(r.get("displayPrediction")) and not (r.get("actionablePick") is True or r.get("officialPick") is True) for r in rows)
    result.update({
        "actionablePickCount": actionable, "lowConfidencePredictionCount": low,
        "nonActionablePredictionCount": low, "requiredGameWinnerPredictionCount": required,
        "displayPredictionCount": displayed,
        "missingDisplayedWinnerPredictions": [r.get("gameKey") or r.get("gameId") for r in rows if not r.get("displayPrediction")],
        "allGamesHaveWinnerPrediction": bool(rows and required == len(rows)),
        "allGamesHaveDisplayedWinnerPrediction": bool(rows and displayed == len(rows)), "noPickCount": 0,
    })
    summary = {
        "enabled": enabled, "modelAvailable": bool(model), "validatedAgainstTarget": valid,
        "evaluatedCount": evaluated, "promotedCount": promoted, "rejectedCount": rejected,
        "threshold": info, "standardThreshold": standard, "guardedThreshold": guarded,
        "runtimeVersion": VERSION, "candidatePolicyVersion": CANDIDATE_POLICY_VERSION,
        "modelVersion": (model or {}).get("version"),
        "featureVectorVersion": (model or {}).get("featureVectorVersion") or FEATURE_VECTOR_VERSION,
        "rowCount": (model or {}).get("rowCount"), "trainingSource": (model or {}).get("trainingSource"),
        "minGuardedPromotions": min_promotions, "maxGuardedPromotions": max_promotions,
        "requiredWinnerPickPreserved": True, "displayPredictionCount": displayed,
        "lowConfidencePredictionCount": low, "finalGuardedStoreRequested": True,
    }
    result["requiredWinnerPredictionDisplay"] = [_card(r) for r in rows if r.get("displayPrediction")]
    result["officialPredictionDisplay"] = result["requiredWinnerPredictionDisplay"]
    result["nonOfficialPredictionDisplay"] = [_card(r) for r in rows if r.get("displayPrediction") and not (r.get("actionablePick") is True or r.get("officialPick") is True)]
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


def _store_final(module, result, requested):
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
