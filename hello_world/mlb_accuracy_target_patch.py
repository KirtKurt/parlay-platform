"""MLB rolling 24-hour accuracy target and signal-score calibration.

Every MLB game is still scored. The 75% target is measured only as a rolling
24-hour average from completed games, not as a guarantee on every single pick.

This patch also reads the latest rolling audit learning adjustments and nudges
future signal scores based on which tags have recently helped or hurt.
"""

import math

try:
    import inqsi_pull_history as history
except Exception:
    history = None

ROLLING_TARGET_ACCURACY_PCT = 75.0
ROLLING_WINDOW_HOURS = 24
ACTIONABLE_MIN_SCORE = 60.0
WATCHLIST_MIN_SCORE = 54.0
BAD_TAGS = {"LOW_PULL_DEPTH", "SINGLE_PULL_BASELINE", "BOOK_DIVERGENCE", "LATE_INSTABILITY"}
REAL_SIGNAL_TAGS = {"STEAM", "RUN_LINE_CONFIRMATION", "RUN_LINE_MOVEMENT", "REVERSAL", "COMPRESSED_MARKET"}
_LEARNING_CACHE = None


def _as_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def _prob_from_score(score):
    prob = 1.0 / (1.0 + math.exp(-(score - 50.0) / 12.0))
    return max(0.05, min(0.95, prob))


def _latest_learning():
    global _LEARNING_CACHE
    if _LEARNING_CACHE is not None:
        return _LEARNING_CACHE
    _LEARNING_CACHE = {}
    try:
        if history is None or history.PULLS is None:
            return _LEARNING_CACHE
        item = history.PULLS.get_item(Key={"PK": "MLB_ROLLING_24H_AUDIT#LATEST", "SK": "LATEST"}).get("Item") or {}
        data = item.get("data") or {}
        _LEARNING_CACHE = data.get("scoreLearning") or {}
    except Exception:
        _LEARNING_CACHE = {}
    return _LEARNING_CACHE


def _learning_adjustment(tags):
    learning = _latest_learning()
    adjustments = (learning.get("adjustments") or {}).get("tagScoreAdjustments") or {}
    total = 0.0
    for tag in tags or []:
        total += _as_float(adjustments.get(str(tag)), 0.0)
    return round(max(-6.0, min(6.0, total)), 2)


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
    learning_adj = _learning_adjustment(tags)
    score_after_learning = max(0.0, min(100.0, raw_score + learning_adj))
    penalty = _calibration_penalty(tags, reversal_count)
    calibrated_score = round(max(0.0, min(100.0, score_after_learning - penalty)), 2)
    calibrated_prob = _prob_from_score(calibrated_score)
    calibrated_prob_pct = round(calibrated_prob * 100.0, 2)
    disqualified = bool(tags & BAD_TAGS)
    book_only = tags == {"BOOK_AGREEMENT"}
    has_real_signal = bool(tags & REAL_SIGNAL_TAGS)

    if calibrated_score >= ACTIONABLE_MIN_SCORE and not disqualified and not book_only and has_real_signal:
        actionability = "ACTIONABLE_PICK"
        reason = "clears_actionable_signal_gate"
        official = True
    elif calibrated_score >= WATCHLIST_MIN_SCORE and not disqualified:
        actionability = "WATCHLIST_ONLY"
        reason = "scored_but_not_actionable"
        official = False
    else:
        actionability = "OBSERVE_ONLY"
        reason = "below_actionable_signal_gate"
        official = False

    out = dict(row)
    out["rawScoreBeforeLearning"] = raw_score
    out["rolling24hLearningAdjustment"] = learning_adj
    out["scoreAfterLearningAdjustment"] = round(score_after_learning, 2)
    out["calibrationPenalty"] = round(penalty, 2)
    out["score"] = calibrated_score
    out["winProbability"] = round(calibrated_prob, 4)
    out["winProbabilityPct"] = calibrated_prob_pct
    out["officialPick"] = official
    out["accuracyTargetEligible"] = official
    out["actionability"] = actionability
    out["actionabilityReason"] = reason
    out["rolling24hAccuracyTarget"] = {
        "targetAccuracyPct": ROLLING_TARGET_ACCURACY_PCT,
        "windowHours": ROLLING_WINDOW_HOURS,
        "measuredBy": "mlb_rolling_24h_audit",
        "note": "The 75% target is evaluated on completed actionable picks over the following rolling 24 hours, not on each individual pick as a guarantee.",
    }
    out["accuracyGatePolicy"] = {
        "actionableMinScore": ACTIONABLE_MIN_SCORE,
        "watchlistMinScore": WATCHLIST_MIN_SCORE,
        "requiresRealSignalBeyondBookAgreement": True,
        "disqualifyingTags": sorted(BAD_TAGS),
    }
    return out


def _summary(predictions):
    official = [row for row in predictions if row.get("officialPick")]
    watch = [row for row in predictions if row.get("actionability") == "WATCHLIST_ONLY"]
    observe = [row for row in predictions if row.get("actionability") == "OBSERVE_ONLY"]
    return {
        "targetAccuracyPct": ROLLING_TARGET_ACCURACY_PCT,
        "windowHours": ROLLING_WINDOW_HOURS,
        "actionablePickCount": len(official),
        "watchlistCount": len(watch),
        "observeOnlyCount": len(observe),
        "actionablePicks": [row.get("predictedWinner") for row in official],
        "policy": "Every game remains scored. The 75% target is measured only by the rolling 24-hour audit of completed actionable picks.",
        "latestLearningApplied": bool(_latest_learning()),
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
        result["rolling24hAccuracyTarget"] = _summary(predictions)
        result["accuracyTarget"] = result["rolling24hAccuracyTarget"]
        result["modelVersion"] = str(result.get("modelVersion") or "") + "+rolling24h-target"
        return result

    module.predict_all = guarded_predict_all
    module._INQSI_MLB_75_TARGET_APPLIED = True
    return module
