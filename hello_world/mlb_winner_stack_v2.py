from __future__ import annotations

import math
from typing import Any, Dict, List

VERSION = "MLB-WINNER-STACK-v2.2-market-confirmed-actionable-lean"

BAD_TAGS = {"LOW_PULL_DEPTH", "SINGLE_PULL_BASELINE", "BOOK_DIVERGENCE", "LATE_INSTABILITY"}
STRONG_TAGS = {"STEAM", "RUN_LINE_CONFIRMATION", "RUN_LINE_MOVEMENT", "BOOK_AGREEMENT"}
CHAOS_TAGS = {"BOOK_DIVERGENCE", "COMPRESSED_MARKET", "UNCONFIRMED_RUN_LINE_MOVE"}


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _prob_from_score(score: float) -> float:
    return _clamp(1.0 / (1.0 + math.exp(-(score - 50.0) / 12.0)), 0.05, 0.95)


def _tier(prob: float, score: float, tags: List[str]) -> str:
    edge = abs(prob - 0.5)
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


def _sig(row: Dict[str, Any], side: str) -> Dict[str, Any]:
    return dict((row.get("homeSignal") if side == "home" else row.get("awaySignal")) or {})


def _selected_and_opponent(row: Dict[str, Any]):
    side = row.get("predictedSide") or "home"
    selected = _sig(row, side)
    other = _sig(row, "away" if side == "home" else "home")
    return selected, other


def _market_component(selected: Dict[str, Any], other: Dict[str, Any]) -> Dict[str, Any]:
    p = _f(selected.get("marketConsensusProbability"), _f(selected.get("probLatest"), 0.5))
    opp = _f(other.get("marketConsensusProbability"), _f(other.get("probLatest"), 1.0 - p))
    edge = p - opp
    score = _clamp(50.0 + edge * 90.0, 0.0, 100.0)
    return {
        "score": round(score, 2),
        "consensusProbability": round(p, 5),
        "opponentConsensusProbability": round(opp, 5),
        "consensusEdge": round(edge, 5),
        "source": "de_vigged_moneyline_consensus",
    }


def _movement_component(selected: Dict[str, Any], market: Dict[str, Any]) -> Dict[str, Any]:
    tags = set(selected.get("tags") or [])
    score = _f(selected.get("optimizedWinnerScore", selected.get("score")), 50.0)
    rev = int(_f(selected.get("reversalCount"), 0.0))
    market_edge = _f(market.get("consensusEdge"), 0.0)
    market_prob = _f(market.get("consensusProbability"), _f(selected.get("marketConsensusProbability"), 0.5))

    clean_books = "BOOK_AGREEMENT" in tags and "BOOK_DIVERGENCE" not in tags
    clean_steam = "STEAM" in tags and clean_books and rev <= 1 and market_edge >= 0.04
    clean_runline_confirmation = (
        "RUN_LINE_CONFIRMATION" in tags
        and clean_books
        and rev <= 1
        and market_edge >= 0.05
        and "COMPRESSED_MARKET" not in tags
        and "UNCONFIRMED_RUN_LINE_MOVE" not in tags
    )

    if clean_steam:
        score += 2.0
    elif "STEAM" in tags and ("BOOK_DIVERGENCE" in tags or rev >= 3 or market_edge < 0.02):
        score -= 1.5

    if clean_runline_confirmation:
        score += 2.0
    elif "RUN_LINE_CONFIRMATION" in tags:
        score -= 2.5
    elif "RUN_LINE_MOVEMENT" in tags:
        score -= 1.0
        if rev >= 2 or "COMPRESSED_MARKET" in tags:
            score -= 1.75

    if clean_books:
        score += 1.5 if market_prob >= 0.52 else 0.5

    if "BOOK_DIVERGENCE" in tags:
        score -= 6.0
    if "COMPRESSED_MARKET" in tags:
        score -= 2.5
    if "UNCONFIRMED_RUN_LINE_MOVE" in tags:
        score -= 2.0

    if rev >= 5:
        score -= 7.0
    elif rev >= 3:
        score -= 5.0
    elif rev == 2:
        score -= 2.5

    if market_prob < 0.50:
        score -= 5.0

    return {
        "score": round(_clamp(score, 0.0, 100.0), 2),
        "tags": sorted(tags),
        "reversalCount": rev,
        "runLineMovement": selected.get("runLineMovement"),
        "marketEdge": round(market_edge, 5),
        "cleanSteam": clean_steam,
        "cleanRunLineConfirmation": clean_runline_confirmation,
        "source": "line_movement_steam_resistance_reversal_runline_confirmation_risk_calibrated",
    }


def _fundamental_component(row: Dict[str, Any], selected: Dict[str, Any], other: Dict[str, Any]) -> Dict[str, Any]:
    optimizer = row.get("winnerOptimizer") or {}
    applied = bool(optimizer.get("fundamentalsApplied"))
    fundamentals = optimizer.get("fundamentals") or {}
    if not applied:
        return {
            "score": 50.0,
            "applied": False,
            "mode": optimizer.get("fundamentalsMode") or "NEUTRAL_NOT_ENABLED",
            "source": "neutral_fundamentals_until_live_provider_enabled",
        }
    selected_adj = _f(selected.get("fundamentalsAdjustment"), 0.0)
    other_adj = _f(other.get("fundamentalsAdjustment"), 0.0)
    edge = selected_adj - other_adj
    score = _clamp(50.0 + edge * 3.0, 35.0, 65.0)
    return {
        "score": round(score, 2),
        "applied": True,
        "mode": optimizer.get("fundamentalsMode") or "TIMESTAMPED_FUNDAMENTALS_V2",
        "edge": round(edge, 2),
        "details": fundamentals,
        "source": "team_power_starting_pitcher_bullpen_lineup_fundamentals",
    }


def _weights(fundamentals_applied: bool) -> Dict[str, float]:
    if fundamentals_applied:
        return {"market": 0.56, "movement": 0.25, "fundamentals": 0.19}
    return {"market": 0.70, "movement": 0.30, "fundamentals": 0.0}


def _calibrated_probability(raw_prob: float, market_prob: float, tags: List[str], fundamentals_applied: bool, selected: Dict[str, Any]) -> Dict[str, Any]:
    anchor_weight = 0.43 if fundamentals_applied else 0.54
    p = raw_prob * (1.0 - anchor_weight) + market_prob * anchor_weight
    tagset = set(tags or [])
    rev = int(_f(selected.get("reversalCount"), 0.0))
    market_edge = market_prob - 0.5
    shrink = 0.0
    risk_reasons: List[str] = []

    if tagset & BAD_TAGS:
        shrink += 0.07
        risk_reasons.append("hard_weakness_tag")
    if tagset & CHAOS_TAGS:
        shrink += 0.05
        risk_reasons.append("chaos_or_unconfirmed_movement")
    if rev >= 5:
        shrink += 0.08
        risk_reasons.append("very_high_reversal_count")
    elif rev >= 3:
        shrink += 0.05
        risk_reasons.append("high_reversal_count")
    elif rev == 2:
        shrink += 0.025
        risk_reasons.append("moderate_reversal_count")
    if "RUN_LINE_CONFIRMATION" in tagset and "BOOK_AGREEMENT" not in tagset:
        shrink += 0.05
        risk_reasons.append("run_line_confirmation_without_book_agreement")
    if "RUN_LINE_MOVEMENT" in tagset and "RUN_LINE_CONFIRMATION" not in tagset:
        shrink += 0.025
        risk_reasons.append("unconfirmed_run_line_movement")
    if market_edge < 0.02:
        shrink += 0.035
        risk_reasons.append("weak_market_edge")

    if "BOOK_AGREEMENT" in tagset and rev <= 1 and market_edge >= 0.04:
        shrink = max(0.0, shrink - 0.025)

    p = 0.5 + (p - 0.5) * (1.0 - _clamp(shrink, 0.0, 0.24))
    return {
        "rawProbability": round(raw_prob, 4),
        "marketAnchorProbability": round(market_prob, 4),
        "calibratedProbability": round(_clamp(p, 0.05, 0.95), 4),
        "shrinkageToward50": round(shrink, 4),
        "riskReasons": risk_reasons,
        "method": "ensemble_probability_anchored_to_de_vigged_market_consensus_with_false_confirmation_shrinkage",
    }


def _actionability(prob: float, score: float, tier: str, tags: List[str], selected: Dict[str, Any], market: Dict[str, Any], fundamentals: Dict[str, Any], calibration: Dict[str, Any]) -> Dict[str, Any]:
    tagset = set(tags or [])
    rev = int(_f(selected.get("reversalCount"), 0.0))
    market_edge = _f(market.get("consensusEdge"), 0.0)
    market_prob = _f(market.get("consensusProbability"), 0.5)
    fundamentals_score = _f(fundamentals.get("score"), 50.0)
    fundamentals_edge = _f(fundamentals.get("edge"), 0.0)
    risk_reasons = list(calibration.get("riskReasons") or [])
    weak = bool(tagset & {"LOW_PULL_DEPTH", "SINGLE_PULL_BASELINE", "BOOK_DIVERGENCE", "UNCONFIRMED_RUN_LINE_MOVE"})

    if rev >= 3:
        weak = True
        risk_reasons.append("reversal_count_actionability_block")
    if "COMPRESSED_MARKET" in tagset and market_edge < 0.05:
        weak = True
        risk_reasons.append("compressed_market_low_edge_block")
    if "RUN_LINE_CONFIRMATION" in tagset and "BOOK_AGREEMENT" not in tagset:
        weak = True
        risk_reasons.append("false_confirmation_block")

    fundamentals_support = fundamentals_score >= 55.0 or fundamentals_edge >= 1.5
    low_risk_market_lean = (
        prob >= 0.60
        and score >= 57.0
        and market_edge >= 0.08
        and market_prob >= 0.54
        and "BOOK_AGREEMENT" in tagset
        and rev <= 1
        and not weak
        and not risk_reasons
    )
    fundamentals_confirmed_lean = (
        prob >= 0.66
        and score >= 60.0
        and market_edge >= 0.12
        and market_prob >= 0.58
        and "BOOK_AGREEMENT" in tagset
        and rev <= 2
        and fundamentals_support
        and not (tagset & {"BOOK_DIVERGENCE", "UNCONFIRMED_RUN_LINE_MOVE", "COMPRESSED_MARKET"})
    )

    if tier in {"Premium", "Solid"} and prob >= 0.64 and score >= 64 and market_edge >= 0.08 and not weak:
        return {
            "actionablePick": True,
            "actionability": "ACTIONABLE_WINNER_PICK",
            "reason": "premium_or_solid_edge_with_market_confirmation_and_no_hard_weakness",
            "riskReasons": risk_reasons,
        }
    if low_risk_market_lean or fundamentals_confirmed_lean:
        return {
            "actionablePick": True,
            "actionability": "ACTIONABLE_MARKET_CONFIRMED_LEAN",
            "reason": "lean_promoted_by_market_confirmation_with_low_risk_or_fundamental_support",
            "riskReasons": risk_reasons,
        }
    if tier == "Lean" and prob >= 0.60 and score >= 58 and market_edge >= 0.05 and not weak:
        return {
            "actionablePick": False,
            "actionability": "WATCHLIST_LEAN",
            "reason": "edge_exists_but_not_strong_enough_for_primary_actionable_bucket",
            "riskReasons": risk_reasons,
        }
    return {
        "actionablePick": False,
        "actionability": "PASS_NO_PICK",
        "reason": "coin_flip_pass_or_hard_weakness_present",
        "riskReasons": risk_reasons,
    }


def enhance_prediction(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row or {})
    selected, other = _selected_and_opponent(out)
    market = _market_component(selected, other)
    movement = _movement_component(selected, market)
    fundamentals = _fundamental_component(out, selected, other)
    weights = _weights(bool(fundamentals.get("applied")))
    ensemble_score = (
        market["score"] * weights["market"]
        + movement["score"] * weights["movement"]
        + fundamentals["score"] * weights["fundamentals"]
    )
    raw_prob = _prob_from_score(ensemble_score)
    tags = sorted(set(out.get("tags") or selected.get("tags") or []))
    calibration = _calibrated_probability(raw_prob, _f(market.get("consensusProbability"), 0.5), tags, bool(fundamentals.get("applied")), selected)
    prob = _f(calibration.get("calibratedProbability"), raw_prob)
    score = round(ensemble_score, 2)
    tier = _tier(prob, score, tags)
    action = _actionability(prob, score, tier, tags, selected, market, fundamentals, calibration)
    actionable = bool(action["actionablePick"])

    out["scoreBeforeWinnerStackV2"] = out.get("score")
    out["winProbabilityBeforeWinnerStackV2"] = out.get("winProbability")
    out["score"] = score
    out["winProbability"] = round(prob, 4)
    out["winProbabilityPct"] = round(prob * 100.0, 2)
    out["confidenceTier"] = tier
    out["officialPrediction"] = True
    out["officialPick"] = actionable
    out["accuracyTargetEligible"] = actionable
    out["actionablePick"] = actionable
    out["actionability"] = action["actionability"]
    out["actionabilityReason"] = action["reason"]
    out["actionabilityRiskReasons"] = action.get("riskReasons") or []
    out["winnerStackV2"] = {
        "applied": True,
        "version": VERSION,
        "components": {"market": market, "movement": movement, "fundamentals": fundamentals},
        "weights": weights,
        "ensembleScore": score,
        "calibration": calibration,
        "discipline": action,
        "policy": "Every game receives a locked prediction; only actionablePick=true rows are primary picks.",
    }
    out["tags"] = sorted(set(tags + ["WINNER_STACK_V2", "CALIBRATED_PROBABILITY"] + (["ACTIONABLE_PICK"] if actionable else ["NO_PICK_DISCIPLINE", "NO_PICK"])))
    return out


def enhance_result(result: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return result
    predictions = [enhance_prediction(row) for row in (result.get("predictions") or [])]
    predictions.sort(key=lambda r: (float(r.get("actionablePick") is True), float(r.get("score") or 0), float(r.get("winProbability") or 0)), reverse=True)
    for idx, row in enumerate(predictions, 1):
        row["rank"] = idx
    out = dict(result)
    out["predictions"] = predictions
    out["count"] = len(predictions)
    actionable = [r for r in predictions if r.get("actionablePick")]
    out["actionablePickCount"] = len(actionable)
    out["noPickCount"] = len([r for r in predictions if not r.get("actionablePick")])
    out["winnerStackV2"] = {
        "applied": True,
        "version": VERSION,
        "layers": [
            "de_vigged_market_baseline",
            "line_movement_engine_risk_calibrated",
            "fundamentals_layer_live_or_neutral",
            "ensemble_weighting_market_first",
            "probability_calibration_false_confirmation_shrinkage",
            "market_confirmed_lean_promotion",
            "actionability_no_pick_discipline",
        ],
        "predictionCount": len(predictions),
        "actionablePickCount": len(actionable),
        "passNoPickCount": len([r for r in predictions if r.get("actionability") == "PASS_NO_PICK"]),
        "watchlistCount": len([r for r in predictions if r.get("actionability") == "WATCHLIST_LEAN"]),
    }
    summary = dict(out.get("rolling24hAccuracyTarget") or out.get("accuracyTarget") or {})
    summary["winnerStackV2"] = out["winnerStackV2"]
    summary["calibrationPolicy"] = "Market-first probabilities, false-confirmation shrinkage, and limited actionable promotion only for clean market-confirmed leans."
    summary["actionablePickCount"] = out["actionablePickCount"]
    summary["noPickCount"] = out["noPickCount"]
    out["rolling24hAccuracyTarget"] = summary
    out["accuracyTarget"] = summary
    if VERSION not in str(out.get("modelVersion") or ""):
        out["modelVersion"] = str(out.get("modelVersion") or "") + "+" + VERSION
    return out


def apply(module: Any):
    if getattr(module, "_INQSI_MLB_WINNER_STACK_V2_APPLIED", False):
        return module
    original = module.predict_all

    def patched_predict_all(*args, **kwargs):
        return enhance_result(original(*args, **kwargs))

    module.predict_all = patched_predict_all
    module._INQSI_MLB_WINNER_STACK_V2_APPLIED = True
    return module
