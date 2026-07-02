from __future__ import annotations

import math
from typing import Any, Dict, List

VERSION = "MLB-WINNER-STACK-v2-market-movement-fundamental-calibrated"

BAD_TAGS = {"LOW_PULL_DEPTH", "SINGLE_PULL_BASELINE", "BOOK_DIVERGENCE", "LATE_INSTABILITY"}
STRONG_TAGS = {"STEAM", "RUN_LINE_CONFIRMATION", "RUN_LINE_MOVEMENT", "BOOK_AGREEMENT"}
CHAOS_TAGS = {"BOOK_DIVERGENCE", "COMPRESSED_MARKET"}


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


def _movement_component(selected: Dict[str, Any]) -> Dict[str, Any]:
    tags = set(selected.get("tags") or [])
    score = _f(selected.get("optimizedWinnerScore", selected.get("score")), 50.0)
    if "STEAM" in tags:
        score += 3.0
    if "RUN_LINE_CONFIRMATION" in tags:
        score += 4.0
    elif "RUN_LINE_MOVEMENT" in tags:
        score += 1.5
    if "BOOK_AGREEMENT" in tags:
        score += 1.5
    if "BOOK_DIVERGENCE" in tags:
        score -= 5.0
    if "COMPRESSED_MARKET" in tags and "RUN_LINE_CONFIRMATION" not in tags:
        score -= 3.0
    rev = int(_f(selected.get("reversalCount"), 0.0))
    if rev >= 3 and "RUN_LINE_CONFIRMATION" not in tags:
        score -= min(8.0, rev * 1.5)
    return {
        "score": round(_clamp(score, 0.0, 100.0), 2),
        "tags": sorted(tags),
        "reversalCount": rev,
        "runLineMovement": selected.get("runLineMovement"),
        "source": "line_movement_steam_resistance_reversal_runline_confirmation",
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
        "mode": optimizer.get("fundamentalsMode") or "SPORTSDATAIO_ENABLED",
        "edge": round(edge, 2),
        "details": fundamentals,
        "source": "team_power_starting_pitcher_bullpen_lineup_fundamentals",
    }


def _weights(fundamentals_applied: bool) -> Dict[str, float]:
    if fundamentals_applied:
        return {"market": 0.50, "movement": 0.30, "fundamentals": 0.20}
    return {"market": 0.62, "movement": 0.38, "fundamentals": 0.0}


def _calibrated_probability(raw_prob: float, market_prob: float, tags: List[str], fundamentals_applied: bool) -> Dict[str, Any]:
    anchor_weight = 0.35 if fundamentals_applied else 0.45
    p = raw_prob * (1.0 - anchor_weight) + market_prob * anchor_weight
    tagset = set(tags or [])
    shrink = 0.0
    if tagset & BAD_TAGS:
        shrink += 0.07
    if tagset & CHAOS_TAGS:
        shrink += 0.04
    rev_penalty = 0.01 * min(5, len([t for t in tags if t == "REVERSAL"]))
    shrink += rev_penalty
    if tagset & {"RUN_LINE_CONFIRMATION", "BOOK_AGREEMENT"}:
        shrink = max(0.0, shrink - 0.03)
    p = 0.5 + (p - 0.5) * (1.0 - _clamp(shrink, 0.0, 0.18))
    return {
        "rawProbability": round(raw_prob, 4),
        "marketAnchorProbability": round(market_prob, 4),
        "calibratedProbability": round(_clamp(p, 0.05, 0.95), 4),
        "shrinkageToward50": round(shrink, 4),
        "method": "ensemble_probability_anchored_to_de_vigged_market_consensus",
    }


def _actionability(prob: float, score: float, tier: str, tags: List[str]) -> Dict[str, Any]:
    tagset = set(tags or [])
    weak = bool(tagset & {"LOW_PULL_DEPTH", "SINGLE_PULL_BASELINE", "BOOK_DIVERGENCE"})
    if tier in {"Premium", "Solid"} and prob >= 0.64 and score >= 64 and not weak:
        return {"actionablePick": True, "actionability": "ACTIONABLE_WINNER_PICK", "reason": "premium_or_solid_calibrated_edge_without_hard_weakness"}
    if tier == "Lean" and prob >= 0.58 and not weak:
        return {"actionablePick": False, "actionability": "WATCHLIST_LEAN", "reason": "edge_exists_but_not_strong_enough_for_primary_actionable_bucket"}
    return {"actionablePick": False, "actionability": "PASS_NO_PICK", "reason": "coin_flip_pass_or_hard_weakness_present"}


def enhance_prediction(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row or {})
    selected, other = _selected_and_opponent(out)
    market = _market_component(selected, other)
    movement = _movement_component(selected)
    fundamentals = _fundamental_component(out, selected, other)
    weights = _weights(bool(fundamentals.get("applied")))
    ensemble_score = (
        market["score"] * weights["market"]
        + movement["score"] * weights["movement"]
        + fundamentals["score"] * weights["fundamentals"]
    )
    raw_prob = _prob_from_score(ensemble_score)
    tags = sorted(set(out.get("tags") or selected.get("tags") or []))
    calibration = _calibrated_probability(raw_prob, _f(market.get("consensusProbability"), 0.5), tags, bool(fundamentals.get("applied")))
    prob = _f(calibration.get("calibratedProbability"), raw_prob)
    score = round(ensemble_score, 2)
    tier = _tier(prob, score, tags)
    action = _actionability(prob, score, tier, tags)
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
            "line_movement_engine",
            "fundamentals_layer_live_or_neutral",
            "ensemble_weighting",
            "probability_calibration",
            "actionability_no_pick_discipline",
        ],
        "predictionCount": len(predictions),
        "actionablePickCount": len(actionable),
        "passNoPickCount": len([r for r in predictions if r.get("actionability") == "PASS_NO_PICK"]),
        "watchlistCount": len([r for r in predictions if r.get("actionability") == "WATCHLIST_LEAN"]),
    }
    summary = dict(out.get("rolling24hAccuracyTarget") or out.get("accuracyTarget") or {})
    summary["winnerStackV2"] = out["winnerStackV2"]
    summary["calibrationPolicy"] = "Brier/log-loss ready probabilities, market anchored and shrunk for instability."
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
