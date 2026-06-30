"""MLB individual game-winner optimizer with rolling 24-hour learning.

Priority: choose the correct team for every individual game.

The 75% target is measured as a rolling 24-hour average across all optimized
individual game-winner picks after games are completed. It is not a per-pick
claim and it is not an actionability filter.

This patch optimizes both sides of each game, applies rolling audit learning to
home and away signals, and then re-selects the side with the strongest optimized
winner score.
"""

import math

try:
    import inqsi_pull_history as history
except Exception:
    history = None

ROLLING_TARGET_ACCURACY_PCT = 75.0
ROLLING_WINDOW_HOURS = 24
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


def _confidence_tier(prob: float, score: float, tags):
    edge = abs(float(prob or 0.5) - 0.5)
    if "LOW_PULL_DEPTH" in set(tags or []):
        return "Baseline"
    if score >= 72 and edge >= 0.12:
        return "Premium"
    if score >= 64 and edge >= 0.08:
        return "Solid"
    if score >= 56 and edge >= 0.04:
        return "Lean"
    if score >= 50:
        return "Coin Flip"
    return "Pass"


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


def _tag_combo(tags):
    return "+".join(sorted(set(tags or []))) or "NO_TAGS"


def _learning_adjustment(tags):
    learning = _latest_learning()
    tags = sorted(set(tags or []))
    adjustments = (learning.get("adjustments") or {})
    tag_adjustments = adjustments.get("tagScoreAdjustments") or {}
    combo_adjustments = adjustments.get("tagComboScoreAdjustments") or {}
    total = 0.0
    for tag in tags:
        total += _as_float(tag_adjustments.get(str(tag)), 0.0)
    total += _as_float(combo_adjustments.get(_tag_combo(tags)), 0.0)
    return round(max(-8.0, min(8.0, total)), 2)


def _rule_adjustment(tags, reversal_count=0):
    tags = set(tags or [])
    adj = 0.0

    # Hard weakness penalties. These hurt team selection, not just confidence.
    if "SINGLE_PULL_BASELINE" in tags:
        adj -= 8.0
    if "LOW_PULL_DEPTH" in tags:
        adj -= 5.0
    if "BOOK_DIVERGENCE" in tags:
        adj -= 2.0
    if "LATE_INSTABILITY" in tags:
        adj -= 6.0

    # Reversal alone was a weak pattern on the posted board; confirmation matters.
    if "REVERSAL" in tags and not (tags & {"RUN_LINE_CONFIRMATION", "RUN_LINE_MOVEMENT", "STEAM"}):
        adj -= 2.5
    if reversal_count >= 3:
        adj -= 4.0
    elif reversal_count == 2:
        adj -= 2.0

    # Confirmation signals are positive, but bounded.
    if "RUN_LINE_CONFIRMATION" in tags:
        adj += 2.5
    if "STEAM" in tags and "RUN_LINE_CONFIRMATION" in tags:
        adj += 1.0
    elif "STEAM" in tags:
        adj += 0.25
    if "RUN_LINE_MOVEMENT" in tags and "RUN_LINE_CONFIRMATION" not in tags:
        adj -= 0.75
    if "COMPRESSED_MARKET" in tags and "RUN_LINE_CONFIRMATION" not in tags:
        adj -= 1.5

    # Book agreement alone is useful information, but not enough to dominate.
    if tags == {"BOOK_AGREEMENT"}:
        adj += 0.5

    return round(max(-10.0, min(10.0, adj)), 2)


def _optimized_signal(signal):
    sig = dict(signal or {})
    tags = sorted(set(sig.get("tags") or []))
    raw_score = _as_float(sig.get("score"), 0.0)
    reversal_count = int(_as_float(sig.get("reversalCount"), 0.0))
    learning_adj = _learning_adjustment(tags)
    rule_adj = _rule_adjustment(tags, reversal_count)
    optimized_score = round(max(0.0, min(100.0, raw_score + learning_adj + rule_adj)), 2)
    prob = _prob_from_score(optimized_score)
    sig["rawScoreBeforeWinnerOptimizer"] = raw_score
    sig["rolling24hLearningAdjustment"] = learning_adj
    sig["winnerRuleAdjustment"] = rule_adj
    sig["optimizedWinnerScore"] = optimized_score
    sig["score"] = optimized_score
    sig["winProbability"] = round(prob, 4)
    sig["winProbabilityPct"] = round(prob * 100.0, 2)
    sig["tags"] = tags
    return sig


def optimize_prediction(row):
    home = _optimized_signal(row.get("homeSignal") or {})
    away = _optimized_signal(row.get("awaySignal") or {})
    if not home and not away:
        return row

    old_winner = row.get("predictedWinner")
    old_side = row.get("predictedSide")
    pick = home if _as_float(home.get("optimizedWinnerScore"), -1.0) >= _as_float(away.get("optimizedWinnerScore"), -1.0) else away
    opponent = away if pick.get("side") == "home" else home
    score = _as_float(pick.get("optimizedWinnerScore"), 0.0)
    prob = _as_float(pick.get("winProbability"), _prob_from_score(score))
    tags = sorted(set(pick.get("tags") or []))

    out = dict(row)
    out["selectionBeforeWinnerOptimizer"] = {"predictedWinner": old_winner, "predictedSide": old_side, "score": row.get("score"), "tags": row.get("tags") or []}
    out["predictedSide"] = pick.get("side")
    out["predictedWinner"] = pick.get("team")
    out["opponent"] = opponent.get("team")
    out["score"] = round(score, 2)
    out["winProbability"] = round(prob, 4)
    out["winProbabilityPct"] = round(prob * 100.0, 2)
    out["confidenceTier"] = _confidence_tier(prob, score, tags)
    out["tags"] = tags
    out["homeSignal"] = home
    out["awaySignal"] = away
    out["individualWinnerOptimized"] = True
    out["optimizerFlippedPick"] = (old_winner != pick.get("team"))
    out["officialPick"] = True
    out["accuracyTargetEligible"] = True
    out["actionability"] = "OPTIMIZED_GAME_WINNER_PICK"
    out["actionabilityReason"] = "all_games_are_optimized_for_team_winner_selection"
    out["rolling24hAccuracyTarget"] = {
        "targetAccuracyPct": ROLLING_TARGET_ACCURACY_PCT,
        "windowHours": ROLLING_WINDOW_HOURS,
        "measuredBy": "mlb_rolling_24h_audit",
        "note": "The 75% target is measured across all optimized individual game-winner picks in the following rolling 24 hours.",
    }
    out["winnerOptimizer"] = {
        "applied": True,
        "basis": "compare_home_and_away_optimized_signal_scores",
        "latestLearningApplied": bool(_latest_learning()),
        "homeOptimizedScore": home.get("optimizedWinnerScore"),
        "awayOptimizedScore": away.get("optimizedWinnerScore"),
        "flippedPick": out["optimizerFlippedPick"],
    }
    return out


def _summary(predictions):
    flipped = [row for row in predictions if row.get("optimizerFlippedPick")]
    return {
        "targetAccuracyPct": ROLLING_TARGET_ACCURACY_PCT,
        "windowHours": ROLLING_WINDOW_HOURS,
        "optimizedGameWinnerPickCount": len(predictions),
        "optimizerFlipCount": len(flipped),
        "optimizerFlippedTeams": [row.get("predictedWinner") for row in flipped],
        "policy": "Every game receives an optimized winner selection. The 75% target is measured by the rolling 24-hour audit over all optimized individual picks.",
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
        predictions = [optimize_prediction(row) for row in (result.get("predictions") or [])]
        predictions.sort(key=lambda r: (float(r.get("score") or 0), float(r.get("winProbability") or 0)), reverse=True)
        for idx, row in enumerate(predictions, 1):
            row["rank"] = idx
        result["predictions"] = predictions
        result["count"] = len(predictions)
        result["allGamesOptimizedForWinner"] = True
        result["rolling24hAccuracyTarget"] = _summary(predictions)
        result["accuracyTarget"] = result["rolling24hAccuracyTarget"]
        result["modelVersion"] = str(result.get("modelVersion") or "") + "+individual-winner-optimizer"
        return result

    module.predict_all = guarded_predict_all
    module._INQSI_MLB_75_TARGET_APPLIED = True
    return module
