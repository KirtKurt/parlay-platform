"""MLB individual game-winner optimizer with rolling audit learning.

Every game still receives a visible winner prediction. Official/actionable status
is deliberately left to the downstream signal, directional, integrity, and
validated-ML gates. This module must not turn a weak diagnostic prediction into
an accuracy-target pick merely because a winner is required for display.
"""

import math

try:
    import inqsi_pull_history as history
except Exception:
    history = None

ROLLING_TARGET_ACCURACY_PCT = 90.0
ROLLING_WINDOW_HOURS = 24
RISK_POLICY_VERSION = "MLB-INDIVIDUAL-WINNER-RISK-CALIBRATION-v4-reversal-flip-gate"
BAD_TAGS = {"LOW_PULL_DEPTH", "SINGLE_PULL_BASELINE", "BOOK_DIVERGENCE", "LATE_INSTABILITY"}
REAL_SIGNAL_TAGS = {"STEAM", "RUN_LINE_CONFIRMATION", "RUN_LINE_MOVEMENT", "REVERSAL", "COMPRESSED_MARKET", "BOOK_AGREEMENT"}
UNSTABLE_TAGS = {"BOOK_DIVERGENCE", "COMPRESSED_MARKET", "UNCONFIRMED_RUN_LINE_MOVE", "LATE_INSTABILITY", "RESISTANCE"}
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
    tagset = set(tags or [])
    if "LOW_PULL_DEPTH" in tagset or "SINGLE_PULL_BASELINE" in tagset:
        return "Baseline"
    if score >= 72 and prob >= 0.72 and edge >= 0.12:
        return "Premium"
    if score >= 64 and prob >= 0.64 and edge >= 0.08:
        return "Solid"
    if score >= 56 and prob >= 0.56 and edge >= 0.04:
        return "Lean"
    if score >= 50 and prob >= 0.50:
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
    tagset = set(tags)
    adjustments = learning.get("adjustments") or {}
    tag_adjustments = adjustments.get("tagScoreAdjustments") or {}
    combo_adjustments = adjustments.get("tagComboScoreAdjustments") or {}
    total = 0.0
    for tag in tags:
        total += _as_float(tag_adjustments.get(str(tag)), 0.0)
    total += _as_float(combo_adjustments.get(_tag_combo(tags)), 0.0)

    if "UNCONFIRMED_RUN_LINE_MOVE" in tagset:
        total = min(total, -1.0)
    elif "RUN_LINE_MOVEMENT" in tagset and "RUN_LINE_CONFIRMATION" not in tagset:
        total = min(total, 1.0)
    if "BOOK_DIVERGENCE" in tagset:
        total = min(total, 0.0)
    if "COMPRESSED_MARKET" in tagset and "BOOK_AGREEMENT" not in tagset:
        total = min(total, 0.5)
    if "RESISTANCE" in tagset or "LATE_INSTABILITY" in tagset:
        total = min(total, -2.0)

    return round(max(-8.0, min(8.0, total)), 2)


def _market_probability(signal):
    sig = dict(signal or {})
    return _as_float(sig.get("marketConsensusProbability"), _as_float(sig.get("probLatest"), 0.5))


def _rule_adjustment(signal):
    sig = dict(signal or {})
    tags = set(sig.get("tags") or [])
    reversal_count = int(_as_float(sig.get("reversalCount"), 0.0))
    market_prob = _market_probability(sig)
    market_edge = market_prob - 0.5
    delta = _as_float(sig.get("delta"), 0.0)
    run_line_move = abs(_as_float(sig.get("runLineMovement"), 0.0))
    adj = 0.0

    if "SINGLE_PULL_BASELINE" in tags:
        adj -= 8.0
    if "LOW_PULL_DEPTH" in tags:
        adj -= 5.0
    if "BOOK_DIVERGENCE" in tags:
        adj -= 3.0
    if "LATE_INSTABILITY" in tags:
        adj -= 6.0
    if "RESISTANCE" in tags:
        adj -= 4.0

    if market_prob < 0.50:
        adj -= 3.0
        if reversal_count >= 2:
            adj -= 2.5
    elif market_prob >= 0.54 and "BOOK_AGREEMENT" in tags and reversal_count <= 1:
        adj += 1.0

    if reversal_count >= 5:
        adj -= 7.5
    elif reversal_count >= 3:
        adj -= 5.5
    elif reversal_count == 2:
        adj -= 2.5
    elif reversal_count == 1 and "BOOK_AGREEMENT" not in tags:
        adj -= 0.75

    clean_confirmation = (
        "BOOK_AGREEMENT" in tags
        and reversal_count <= 1
        and market_edge >= 0.03
        and not (tags & UNSTABLE_TAGS)
    )

    if "RUN_LINE_CONFIRMATION" in tags:
        if clean_confirmation:
            adj += 2.0
        else:
            adj -= 2.25

    if "RUN_LINE_MOVEMENT" in tags and "RUN_LINE_CONFIRMATION" not in tags:
        if "BOOK_AGREEMENT" in tags and "STEAM" in tags and reversal_count <= 1 and market_edge >= 0.02:
            adj += 0.25
        else:
            adj -= 1.25
        if run_line_move >= 50 and market_edge < 0.05:
            adj -= 1.0

    if "STEAM" in tags:
        if "BOOK_AGREEMENT" in tags and reversal_count <= 1 and market_edge >= 0.02:
            adj += 1.0
        elif "BOOK_DIVERGENCE" in tags or reversal_count >= 3:
            adj -= 1.75

    if "COMPRESSED_MARKET" in tags:
        adj -= 1.25
        if market_edge < 0.03:
            adj -= 1.0

    aligned_confirmation = clean_confirmation and ("STEAM" in tags or "RUN_LINE_CONFIRMATION" in tags)
    if delta > 0 and reversal_count >= 3 and not aligned_confirmation:
        adj -= 4.0 if reversal_count < 5 else 6.0

    if "BOOK_AGREEMENT" in tags:
        if reversal_count <= 1 and not (tags & UNSTABLE_TAGS):
            adj += 0.75 if market_edge >= 0.02 else 0.25
        elif reversal_count >= 3:
            adj -= 0.5

    return round(max(-16.0, min(12.0, adj)), 2)


def _optimized_signal(signal):
    sig = dict(signal or {})
    tags = sorted(set(sig.get("tags") or []))
    raw_score = _as_float(sig.get("score"), 0.0)
    learning_adj = _learning_adjustment(tags)
    rule_adj = _rule_adjustment(sig)
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


def _previous_signal(home, away, old_side, old_winner):
    if old_side == "home":
        return home
    if old_side == "away":
        return away
    if old_winner and str(home.get("team") or "").lower() == str(old_winner).lower():
        return home
    if old_winner and str(away.get("team") or "").lower() == str(old_winner).lower():
        return away
    return None


def _safe_optimizer_flip(candidate, previous):
    if not candidate or not previous:
        return True, []
    tags = set(candidate.get("tags") or [])
    rev = int(_as_float(candidate.get("reversalCount"), 0.0))
    market_prob = _market_probability(candidate)
    opponent_prob = _market_probability(previous)
    market_edge = market_prob - opponent_prob
    delta = _as_float(candidate.get("delta"), 0.0)
    score_margin = _as_float(candidate.get("optimizedWinnerScore")) - _as_float(previous.get("optimizedWinnerScore"))
    reasons = []

    if tags & BAD_TAGS:
        reasons.append("flip_candidate_hard_weakness")
    if tags & UNSTABLE_TAGS:
        reasons.append("flip_candidate_unstable")
    if rev >= 2:
        reasons.append("flip_candidate_multiple_reversals")
    if market_prob < 0.54 or market_edge < 0.08:
        reasons.append("flip_candidate_insufficient_market_confirmation")
    if score_margin < 6.0:
        reasons.append("flip_candidate_insufficient_score_margin")
    if delta <= 0 and not ({"STEAM", "RUN_LINE_CONFIRMATION"} & tags):
        reasons.append("flip_candidate_no_positive_confirmed_direction")
    if "REVERSAL" in tags and rev >= 1 and not ({"STEAM", "RUN_LINE_CONFIRMATION"} & tags):
        reasons.append("flip_candidate_reversal_without_confirmation")

    return not reasons, reasons


def _preliminary_risks(signal):
    sig = dict(signal or {})
    tags = set(sig.get("tags") or [])
    rev = int(_as_float(sig.get("reversalCount"), 0.0))
    market_prob = _market_probability(sig)
    delta = _as_float(sig.get("delta"), 0.0)
    risks = []
    if tags & BAD_TAGS:
        risks.append("hard_weakness_tag")
    if tags & UNSTABLE_TAGS:
        risks.append("unstable_market_profile")
    if rev >= 5:
        risks.append("very_high_reversal_count")
    elif rev >= 3:
        risks.append("high_reversal_count")
    elif rev == 2:
        risks.append("moderate_reversal_count")
    if market_prob < 0.50:
        risks.append("selected_side_not_market_favorite")
    if delta > 0 and rev >= 3 and not ({"STEAM", "RUN_LINE_CONFIRMATION"} & tags):
        risks.append("positive_move_with_reversal_instability")
    return sorted(set(risks))


def optimize_prediction(row):
    home = _optimized_signal(row.get("homeSignal") or {})
    away = _optimized_signal(row.get("awaySignal") or {})
    if not home and not away:
        return row

    old_winner = row.get("predictedWinner")
    old_side = row.get("predictedSide")
    candidate = home if _as_float(home.get("optimizedWinnerScore"), -1.0) >= _as_float(away.get("optimizedWinnerScore"), -1.0) else away
    previous = _previous_signal(home, away, old_side, old_winner)
    flip_requested = bool(previous and candidate.get("team") != previous.get("team"))
    flip_allowed, flip_block_reasons = _safe_optimizer_flip(candidate, previous)
    pick = candidate
    if flip_requested and not flip_allowed:
        pick = previous

    opponent = away if pick.get("side") == "home" else home
    score = _as_float(pick.get("optimizedWinnerScore"), 0.0)
    prob = _as_float(pick.get("winProbability"), _prob_from_score(score))
    tags = sorted(set(pick.get("tags") or []))
    risks = _preliminary_risks(pick)
    if flip_requested and not flip_allowed:
        risks = sorted(set(risks + ["unsafe_optimizer_flip_blocked"] + flip_block_reasons))

    out = dict(row)
    out["selectionBeforeWinnerOptimizer"] = {
        "predictedWinner": old_winner,
        "predictedSide": old_side,
        "score": row.get("score"),
        "tags": row.get("tags") or [],
    }
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
    out["optimizerFlippedPick"] = bool(old_winner and old_winner != pick.get("team"))
    out["optimizerFlipRequested"] = flip_requested
    out["optimizerFlipAllowed"] = bool(not flip_requested or flip_allowed)
    out["optimizerFlipBlockedReasons"] = flip_block_reasons if flip_requested and not flip_allowed else []

    out["officialPrediction"] = True
    out["platformPick"] = bool(pick.get("team"))
    out["customerVisibleWinnerPick"] = bool(pick.get("team"))
    out["officialPick"] = False
    out["accuracyTargetEligible"] = False
    out["actionablePick"] = False
    out["actionability"] = "OPTIMIZED_WINNER_PREDICTION_PENDING_FINAL_SIGNAL_GATE"
    out["actionabilityReason"] = "visible_winner_prediction_requires_downstream_signal_or_validated_ml_confirmation"
    out["actionabilityRiskReasons"] = risks
    out["rolling24hAccuracyTarget"] = {
        "targetAccuracyPct": ROLLING_TARGET_ACCURACY_PCT,
        "windowHours": ROLLING_WINDOW_HOURS,
        "measuredBy": "mlb_rolling_24h_audit",
        "note": "Only downstream-gated official/actionable selections count toward the 90% target; every game still keeps a visible winner prediction.",
    }
    out["winnerOptimizer"] = {
        "applied": True,
        "basis": "market_signal_plus_multi_window_learning_plus_reversal_flip_safety",
        "latestLearningApplied": bool(_latest_learning()),
        "homeOptimizedScore": home.get("optimizedWinnerScore"),
        "awayOptimizedScore": away.get("optimizedWinnerScore"),
        "flippedPick": out["optimizerFlippedPick"],
        "flipRequested": flip_requested,
        "flipAllowed": bool(not flip_requested or flip_allowed),
        "flipBlockedReasons": out["optimizerFlipBlockedReasons"],
        "riskPolicyVersion": RISK_POLICY_VERSION,
    }
    return out


def _summary(predictions):
    flipped = [row for row in predictions if row.get("optimizerFlippedPick")]
    blocked = [row for row in predictions if row.get("optimizerFlipRequested") and not row.get("optimizerFlipAllowed")]
    return {
        "targetAccuracyPct": ROLLING_TARGET_ACCURACY_PCT,
        "windowHours": ROLLING_WINDOW_HOURS,
        "optimizedGameWinnerPredictionCount": len(predictions),
        "optimizerFlipCount": len(flipped),
        "optimizerFlipBlockedCount": len(blocked),
        "optimizerFlippedTeams": [row.get("predictedWinner") for row in flipped],
        "policy": "Every game receives a visible optimized winner prediction. Official/actionable status is assigned only by downstream risk and validated-ML gates.",
        "latestLearningApplied": bool(_latest_learning()),
        "riskPolicyVersion": RISK_POLICY_VERSION,
    }


def apply(module):
    if getattr(module, "_INQSI_MLB_90_TARGET_APPLIED", False):
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
        result["modelVersion"] = str(result.get("modelVersion") or "") + "+individual-winner-optimizer-risk-v4"
        return result

    module.predict_all = guarded_predict_all
    module._INQSI_MLB_90_TARGET_APPLIED = True
    return module
