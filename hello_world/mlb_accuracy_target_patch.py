"""MLB individual-pick 75% target calibration.

The model should still score every MLB game, but only predictions that clear the
75% target gate are treated as official/actionable picks. Lower-confidence games
remain visible as scored observations and are available for audit.
"""

import math

TARGET_ACCURACY_PCT = 75.0
OFFICIAL_MIN_PROB_PCT = 75.0
OFFICIAL_MIN_SCORE = 62.0
WATCHLIST_MIN_PROB_PCT = 68.0
WATCHLIST_MIN_SCORE = 58.0
BAD_TAGS = {"LOW_PULL_DEPTH", "SINGLE_PULL_BASELINE", "BOOK_DIVERGENCE", "LATE_INSTABILITY"}
REAL_SIGNAL_TAGS = {"STEAM", "RUN_LINE_CONFIRMATION", "RUN_LINE_MOVEMENT", "REVERSAL", "COMPRESSED_MARKET"}


def _as_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def _prob_from_score(score):
    prob = 1.0 / (1.0 + math.exp(-(score - 50.0) / 12.0))
    return max(0.05, min(0.95, prob))


def _calibration_penalty(tags, reversal_count=0):
    tags = set(tags or [])
    penalty = 0.0
    if tags == {"BOOK_AGREEMENT"}:
        penalty += 5.0
    if "BOOK_DIVERGENCE" in tags:
        penalty += 7.0
    if "LOW_PULL_DEPTH" in tags:
        penalty += 6.0
    if "SINGLE_PULL_BASELINE" in tags:
        penalty += 8.0
    if reversal_count >= 3:
        penalty += 6.0
    elif reversal_count == 2:
        penalty += 3.0
    if "RUN_LINE_MOVEMENT" in tags and "RUN_LINE_CONFIRMATION" not in tags and "STEAM" not in tags:
        penalty += 2.5
    return penalty


def classify(row):
    tags = set(row.get("tags") or [])
    chosen_side = row.get("predictedSide")
    signal = row.get("homeSignal") if chosen_side == "home" else row.get("awaySignal")
    signal = signal or {}
    raw_score = _as_float(row.get("score"), 0.0)
    reversal_count = int(_as_float(signal.get("reversalCount"), 0.0))
    penalty = _calibration_penalty(tags, reversal_count)
    calibrated_score = round(max(0.0, min(100.0, raw_score - penalty)), 2)
    calibrated_prob = _prob_from_score(calibrated_score)
    calibrated_prob_pct = round(calibrated_prob * 100.0, 2)
    disqualified = bool(tags & BAD_TAGS)
    book_only = tags == {"BOOK_AGREEMENT"}
    has_real_signal = bool(tags & REAL_SIGNAL_TAGS)

    if calibrated_prob_pct >= OFFICIAL_MIN_PROB_PCT and calibrated_score >= OFFICIAL_MIN_SCORE and not disqualified and not book_only and has_real_signal:
        actionability = "OFFICIAL_PICK_75_TARGET"
        reason = "clears_75pct_target_gate"
        official = True
    elif calibrated_prob_pct >= WATCHLIST_MIN_PROB_PCT and calibrated_score >= WATCHLIST_MIN_SCORE and not disqualified and not book_only:
        actionability = "WATCHLIST_ONLY"
        reason = "below_75pct_target_but_directionally_interesting"
        official = False
    else:
        actionability = "OBSERVE_ONLY"
        reason = "below_75pct_official_pick_gate"
        official = False

    out = dict(row)
    out["rawScoreBefore75TargetCalibration"] = raw_score
    out["rawWinProbabilityPctBefore75TargetCalibration"] = row.get("winProbabilityPct")
    out["calibrationPenalty"] = round(penalty, 2)
    out["score"] = calibrated_score
    out["winProbability"] = round(calibrated_prob, 4)
    out["winProbabilityPct"] = calibrated_prob_pct
    out["targetAccuracyPct"] = TARGET_ACCURACY_PCT
    out["officialPick"] = official
    out["accuracyTargetEligible"] = official
    out["actionability"] = actionability
    out["actionabilityReason"] = reason
    out["accuracyGatePolicy"] = {
        "officialMinProbabilityPct": OFFICIAL_MIN_PROB_PCT,
        "officialMinScore": OFFICIAL_MIN_SCORE,
        "requiresRealSignalBeyondBookAgreement": True,
        "disqualifyingTags": sorted(BAD_TAGS),
    }
    return out


def _summary(predictions):
    official = [row for row in predictions if row.get("officialPick")]
    watch = [row for row in predictions if row.get("actionability") == "WATCHLIST_ONLY"]
    observe = [row for row in predictions if row.get("actionability") == "OBSERVE_ONLY"]
    return {
        "targetAccuracyPct": TARGET_ACCURACY_PCT,
        "officialPickCount": len(official),
        "watchlistCount": len(watch),
        "observeOnlyCount": len(observe),
        "officialPicks": [row.get("predictedWinner") for row in official],
        "policy": "Every game remains scored. Only OFFICIAL_PICK_75_TARGET rows count as actionable individual picks toward the 75% accuracy target.",
    }


def apply(module):
    if getattr(module, "_INQSI_MLB_75_TARGET_APPLIED", False):
        return module
    original_predict_all = module.predict_all

    def guarded_predict_all(*args, **kwargs):
        result = original_predict_all(*args, **kwargs)
        if not isinstance(result, dict):
            return result
        predictions = [classify(row) for row in (result.get("predictions") or [])]
        predictions.sort(key=lambda r: (float(r.get("score") or 0), float(r.get("winProbability") or 0)), reverse=True)
        for idx, row in enumerate(predictions, 1):
            row["rank"] = idx
        result["predictions"] = predictions
        result["count"] = len(predictions)
        result["accuracyTarget"] = _summary(predictions)
        result["modelVersion"] = str(result.get("modelVersion") or "") + "+75pct-target"
        return result

    module.predict_all = guarded_predict_all
    module._INQSI_MLB_75_TARGET_APPLIED = True
    return module
