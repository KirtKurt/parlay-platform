from __future__ import annotations

import math
import os
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    import mlb_fundamentals_engine
except Exception:
    mlb_fundamentals_engine = None

# If the SportsDataIO key/feed is available this layer uses it. If it is not
# available, the row is explicitly tagged as missing fundamentals instead of
# pretending market movement is a true fundamental input.
USE_FUNDAMENTALS = os.environ.get("INQSI_MLB_USE_SPORTSDATAIO_FUNDAMENTALS", "true").lower() in {"1", "true", "yes"}
CALIBRATION_ENABLED = os.environ.get("INQSI_MLB_CALIBRATION_ENABLED", "true").lower() in {"1", "true", "yes"}
NO_PICK_ENABLED = os.environ.get("INQSI_MLB_NO_PICK_DISCIPLINE_ENABLED", "true").lower() in {"1", "true", "yes"}
MIN_ACTIONABLE_PULLS = int(os.environ.get("INQSI_MLB_MIN_ACTIONABLE_PULLS", "12"))
MIN_ACTIONABLE_PROB = float(os.environ.get("INQSI_MLB_MIN_ACTIONABLE_CALIBRATED_PROB", "0.59"))
MIN_ACTIONABLE_SCORE = float(os.environ.get("INQSI_MLB_MIN_ACTIONABLE_SCORE", "56"))
MAX_ACTIONABLE_RISK = float(os.environ.get("INQSI_MLB_MAX_ACTIONABLE_RISK", "0.18"))
PATCH_VERSION = "MLB-FUNDAMENTALS-CALIBRATION-NO-PICK-v1"
_FUNDAMENTALS_CACHE: Dict[str, Dict[str, Dict[str, Any]]] = {}

TEAM_ALIASES = {
    "arizona diamondbacks": ["ari"], "atlanta braves": ["atl"], "baltimore orioles": ["bal"], "boston red sox": ["bos"],
    "chicago cubs": ["chc"], "chicago white sox": ["cws", "chw"], "cincinnati reds": ["cin"], "cleveland guardians": ["cle"],
    "colorado rockies": ["col"], "detroit tigers": ["det"], "houston astros": ["hou"], "kansas city royals": ["kc", "kan"],
    "los angeles angels": ["laa", "ana"], "los angeles dodgers": ["lad", "la"], "miami marlins": ["mia"], "milwaukee brewers": ["mil"],
    "minnesota twins": ["min"], "new york mets": ["nym"], "new york yankees": ["nyy"], "oakland athletics": ["oak", "ath"],
    "athletics": ["oak", "ath"], "philadelphia phillies": ["phi"], "pittsburgh pirates": ["pit"], "san diego padres": ["sd", "sdp"],
    "san francisco giants": ["sf", "sfg"], "seattle mariners": ["sea"], "st louis cardinals": ["stl"], "st. louis cardinals": ["stl"],
    "tampa bay rays": ["tb", "tbr"], "texas rangers": ["tex"], "toronto blue jays": ["tor"], "washington nationals": ["was", "wsh"],
}


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(round(float(value)))
    except Exception:
        return default


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _norm(value: Any) -> str:
    return " ".join(str(value or "").lower().replace(".", "").strip().split())


def _candidates(value: Any) -> set[str]:
    base = _norm(value)
    out = {base, base.replace(" ", "")} if base else set()
    for alias in TEAM_ALIASES.get(base, []):
        out.add(_norm(alias))
    return {x for x in out if x}


def _prob_from_score(score: float) -> float:
    value = 1.0 / (1.0 + math.exp(-(score - 50.0) / 12.0))
    return _clamp(value, 0.05, 0.95)


def _tier(prob: float, score: float, tags: Iterable[str]) -> str:
    edge = abs(float(prob or 0.5) - 0.5)
    tag_set = set(tags or [])
    if "LOW_PULL_DEPTH" in tag_set or "INSUFFICIENT_HISTORY" in tag_set:
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


def _row_team(row: Dict[str, Any], side: str) -> Any:
    sig = row.get("homeSignal") if side == "home" else row.get("awaySignal")
    sig = sig or {}
    return row.get("homeTeam" if side == "home" else "awayTeam") or sig.get("team")


def _row_slate_date(row: Dict[str, Any]) -> Optional[str]:
    for key in ("slate_date", "game_date_et", "gameDateEt"):
        value = row.get(key)
        if value:
            return str(value)[:10]
    dt_raw = row.get("commenceTime") or row.get("commence_time")
    if dt_raw:
        try:
            dt = datetime.fromisoformat(str(dt_raw).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            # SportsDataIO game dates are slate-local; ET conversion is handled by the fundamentals engine default.
            return dt.astimezone(timezone.utc).date().isoformat()
        except Exception:
            return None
    return None


def _fundamentals_index(slate_date: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    global _FUNDAMENTALS_CACHE
    cache_key = slate_date or "__default_today__"
    if cache_key in _FUNDAMENTALS_CACHE:
        return _FUNDAMENTALS_CACHE[cache_key]
    _FUNDAMENTALS_CACHE[cache_key] = {}
    if not USE_FUNDAMENTALS or mlb_fundamentals_engine is None:
        return _FUNDAMENTALS_CACHE[cache_key]
    try:
        if slate_date:
            preview = mlb_fundamentals_engine.slate_fundamentals_preview(date_yyyy_mm_dd=slate_date)
        else:
            preview = mlb_fundamentals_engine.slate_fundamentals_preview()
        if not isinstance(preview, dict) or not preview.get("ok"):
            return _FUNDAMENTALS_CACHE[cache_key]
        for game in preview.get("games") or []:
            for away in _candidates(game.get("awayTeam")):
                for home in _candidates(game.get("homeTeam")):
                    _FUNDAMENTALS_CACHE[cache_key][f"{away}|{home}"] = game
    except Exception:
        _FUNDAMENTALS_CACHE[cache_key] = {}
    return _FUNDAMENTALS_CACHE[cache_key]


def _match_fundamentals(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not USE_FUNDAMENTALS:
        return None
    slate_date = _row_slate_date(row)
    index = _fundamentals_index(slate_date)
    for away in _candidates(_row_team(row, "away")):
        for home in _candidates(_row_team(row, "home")):
            found = index.get(f"{away}|{home}")
            if found:
                return found
    return None


def _signal(row: Dict[str, Any], side: Optional[str] = None) -> Dict[str, Any]:
    selected = side or row.get("predictedSide") or "home"
    return dict((row.get("homeSignal") if selected == "home" else row.get("awaySignal")) or {})


def _apply_to_signal(sig: Dict[str, Any], adjustment: float) -> Dict[str, Any]:
    sig = dict(sig or {})
    adjustment = round(_clamp(adjustment, -8.0, 8.0), 2)
    old_score = _num(sig.get("optimizedWinnerScore", sig.get("score")), 0.0)
    new_score = round(_clamp(old_score + adjustment, 0.0, 100.0), 2)
    p = _prob_from_score(new_score)
    sig["optimizedWinnerScoreBeforeFundamentals"] = old_score
    sig["fundamentalsAdjustment"] = adjustment
    sig["optimizedWinnerScore"] = new_score
    sig["score"] = new_score
    sig["winProbability"] = round(p, 4)
    sig["winProbabilityPct"] = round(p * 100.0, 2)
    return sig


def _apply_fundamentals(row: Dict[str, Any]) -> Dict[str, Any]:
    fundamentals = _match_fundamentals(row)
    out = dict(row)
    prior = dict(out.get("winnerOptimizer") or {})
    if not fundamentals:
        prior["fundamentalsApplied"] = False
        prior["fundamentalsMode"] = "SPORTSDATAIO_UNAVAILABLE" if USE_FUNDAMENTALS else "DISABLED_ODDS_API_ONLY"
        prior["basis"] = prior.get("basis") or "market_signal_plus_multi_window_learning"
        out["winnerOptimizer"] = prior
        tags = sorted(set((out.get("tags") or []) + ["MISSING_FUNDAMENTALS"]))
        out["tags"] = tags
        out["fundamentalsLayer"] = {
            "available": False,
            "applied": False,
            "mode": prior["fundamentalsMode"],
            "slateDate": _row_slate_date(row),
            "message": "No verified MLB fundamentals package matched this game. Market signal remains primary.",
        }
        return out

    home_adj = _num(((fundamentals.get("homeFundamentals") or {}).get("sideFundamentalsScore")), 0.0)
    away_adj = _num(((fundamentals.get("awayFundamentals") or {}).get("sideFundamentalsScore")), 0.0)
    home = _apply_to_signal(out.get("homeSignal") or {}, home_adj)
    away = _apply_to_signal(out.get("awaySignal") or {}, away_adj)
    pick = home if _num(home.get("optimizedWinnerScore"), -1.0) >= _num(away.get("optimizedWinnerScore"), -1.0) else away
    opponent = away if pick.get("side") == "home" else home
    score = _num(pick.get("optimizedWinnerScore"), 0.0)
    p = _num(pick.get("winProbability"), _prob_from_score(score))
    tags = sorted(set((pick.get("tags") or []) + ["FUNDAMENTALS_APPLIED"]))

    out["homeSignal"] = home
    out["awaySignal"] = away
    out["predictedSide"] = pick.get("side")
    out["predictedWinner"] = pick.get("team")
    out["opponent"] = opponent.get("team")
    out["score"] = round(score, 2)
    out["winProbability"] = round(p, 4)
    out["winProbabilityPct"] = round(p * 100.0, 2)
    out["confidenceTier"] = _tier(p, score, tags)
    out["tags"] = tags
    out["optimizerFlippedByFundamentals"] = row.get("predictedWinner") != out.get("predictedWinner")
    prior["fundamentalsApplied"] = True
    prior["fundamentalsMode"] = "SPORTSDATAIO_ENABLED"
    prior["fundamentals"] = {
        "matchup": fundamentals.get("matchup"),
        "fundamentalsLean": fundamentals.get("fundamentalsLean"),
        "fundamentalsEdgeHomeMinusAway": fundamentals.get("fundamentalsEdgeHomeMinusAway"),
        "homeAdjustment": home.get("fundamentalsAdjustment"),
        "awayAdjustment": away.get("fundamentalsAdjustment"),
        "slateDate": _row_slate_date(row),
        "weights": "team_power_35pct_starter_45pct_bullpen_15pct_lineup_5pct",
    }
    prior["basis"] = "market_signal_plus_multi_window_learning_plus_mlb_fundamentals"
    prior["homeOptimizedScore"] = home.get("optimizedWinnerScore")
    prior["awayOptimizedScore"] = away.get("optimizedWinnerScore")
    out["winnerOptimizer"] = prior
    out["fundamentalsLayer"] = {"available": True, "applied": True, "provider": "SportsDataIO", "details": prior["fundamentals"]}
    return out


def _risk_penalty(row: Dict[str, Any]) -> Tuple[float, List[str]]:
    pick = _signal(row)
    tags = set(row.get("tags") or []) | set(pick.get("tags") or [])
    reasons: List[str] = []
    penalty = 0.0
    pull_count = _int(row.get("pullCountForGame"), 0)
    if pull_count < MIN_ACTIONABLE_PULLS:
        penalty += 0.07
        reasons.append("LOW_PULL_DEPTH")
    elif pull_count < MIN_ACTIONABLE_PULLS * 2:
        penalty += 0.025
        reasons.append("MODERATE_PULL_DEPTH")
    div = _num(pick.get("bookDivergence"), 0.0)
    rev = _int(pick.get("reversalCount"), 0)
    gap = _num(pick.get("latestGap"), 0.0)
    if div >= 0.04 or "BOOK_DIVERGENCE" in tags:
        penalty += 0.045
        reasons.append("BOOK_DIVERGENCE")
    elif div >= 0.025:
        penalty += 0.02
        reasons.append("BOOK_DISAGREEMENT")
    if rev >= 4:
        penalty += 0.06
        reasons.append("HIGH_REVERSAL_COUNT")
    elif rev >= 2 or "REVERSAL" in tags:
        penalty += 0.025
        reasons.append("REVERSAL_RISK")
    if gap < 0.05 or "COMPRESSED_MARKET" in tags:
        penalty += 0.04
        reasons.append("COMPRESSED_MARKET")
    if "MISSING_FUNDAMENTALS" in tags:
        penalty += 0.02
        reasons.append("MISSING_FUNDAMENTALS")
    if "LOW_PULL_DEPTH" in tags or "INSUFFICIENT_HISTORY" in tags:
        penalty += 0.04
        reasons.append("INSUFFICIENT_HISTORY")
    return round(_clamp(penalty, 0.0, 0.30), 4), sorted(set(reasons))


def _calibrate(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row)
    if not CALIBRATION_ENABLED:
        out["calibration"] = {"enabled": False, "version": PATCH_VERSION}
        return out
    pick = _signal(out)
    raw_prob = _clamp(_num(out.get("winProbability"), 0.5), 0.05, 0.95)
    market_prob = _clamp(_num(pick.get("marketConsensusProbability"), raw_prob), 0.05, 0.95)
    score_prob = _prob_from_score(_num(out.get("score"), 50.0))
    penalty, reasons = _risk_penalty(out)
    fundamentals = out.get("fundamentalsLayer") or {}
    fundamentals_boost = 0.0
    if fundamentals.get("applied"):
        lean = ((fundamentals.get("details") or {}).get("fundamentalsLean") or "").lower()
        if lean and lean == str(out.get("predictedSide") or "").lower():
            fundamentals_boost = 0.012
        elif lean in {"home", "away"}:
            fundamentals_boost = -0.025
    raw_edge = raw_prob - 0.5
    market_edge = market_prob - 0.5
    score_edge = score_prob - 0.5
    blended_edge = raw_edge * 0.35 + market_edge * 0.45 + score_edge * 0.20
    shrinkage = _clamp(0.18 + penalty, 0.18, 0.48)
    calibrated = 0.5 + blended_edge * (1.0 - shrinkage)
    calibrated += fundamentals_boost if calibrated >= 0.5 else -fundamentals_boost
    calibrated = round(_clamp(calibrated, 0.05, 0.92), 4)
    out["rawWinProbabilityBeforeCalibration"] = raw_prob
    out["winProbability"] = calibrated
    out["winProbabilityPct"] = round(calibrated * 100.0, 2)
    out["calibratedWinProbability"] = calibrated
    out["confidenceTier"] = _tier(calibrated, _num(out.get("score"), 0.0), out.get("tags") or [])
    out["calibration"] = {
        "enabled": True,
        "version": PATCH_VERSION,
        "method": "market_consensus_score_blend_with_risk_shrinkage",
        "rawProbability": raw_prob,
        "marketConsensusProbability": market_prob,
        "scoreImpliedProbability": round(score_prob, 4),
        "calibratedProbability": calibrated,
        "shrinkage": round(shrinkage, 4),
        "riskPenalty": penalty,
        "riskReasons": reasons,
        "fundamentalsBoost": fundamentals_boost,
        "note": "Calibration intentionally compresses aggressive raw scores toward market consensus and penalizes instability.",
    }
    return out


def _no_pick(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row)
    if not NO_PICK_ENABLED:
        out["pickDiscipline"] = {"enabled": False, "version": PATCH_VERSION}
        out["officialPick"] = True
        out["actionability"] = "PREDICTION_ONLY_NO_DISCIPLINE_GATE"
        return out
    calibrated = _num(out.get("calibratedWinProbability", out.get("winProbability")), 0.5)
    score = _num(out.get("score"), 0.0)
    pull_count = _int(out.get("pullCountForGame"), 0)
    confidence = str(out.get("confidenceTier") or "")
    calibration = out.get("calibration") or {}
    risk = _num(calibration.get("riskPenalty"), 0.0)
    reasons = list(calibration.get("riskReasons") or [])
    no_pick_reasons: List[str] = []
    if pull_count < MIN_ACTIONABLE_PULLS:
        no_pick_reasons.append("needs_more_pull_depth")
    if calibrated < MIN_ACTIONABLE_PROB:
        no_pick_reasons.append("calibrated_probability_below_actionable_threshold")
    if score < MIN_ACTIONABLE_SCORE:
        no_pick_reasons.append("score_below_actionable_threshold")
    if confidence in {"Pass", "Coin Flip", "Baseline"}:
        no_pick_reasons.append(f"confidence_tier_{confidence.lower().replace(' ', '_')}")
    if risk > MAX_ACTIONABLE_RISK:
        no_pick_reasons.append("market_instability_risk_too_high")
    if "MISSING_FUNDAMENTALS" in set(out.get("tags") or []) and calibrated < 0.66:
        no_pick_reasons.append("missing_fundamentals_requires_stronger_market_edge")

    actionable = not no_pick_reasons
    if actionable and calibrated >= 0.68 and score >= 66 and risk <= 0.12:
        level = "STRONG_ACTIONABLE_PICK"
    elif actionable:
        level = "ACTIONABLE_LEAN_PICK"
    else:
        level = "NO_PICK"
    out["officialPick"] = bool(actionable)
    out["actionablePick"] = bool(actionable)
    out["accuracyTargetEligible"] = bool(actionable)
    out["actionability"] = level
    out["actionabilityReason"] = "passes_calibration_and_no_pick_gate" if actionable else ";".join(sorted(set(no_pick_reasons)))
    out["pickDiscipline"] = {
        "enabled": True,
        "version": PATCH_VERSION,
        "actionable": bool(actionable),
        "level": level,
        "thresholds": {
            "minPulls": MIN_ACTIONABLE_PULLS,
            "minCalibratedProbability": MIN_ACTIONABLE_PROB,
            "minScore": MIN_ACTIONABLE_SCORE,
            "maxRiskPenalty": MAX_ACTIONABLE_RISK,
        },
        "calibratedProbability": calibrated,
        "score": score,
        "pullCountForGame": pull_count,
        "riskPenalty": risk,
        "riskReasons": reasons,
        "noPickReasons": sorted(set(no_pick_reasons)),
        "rule": "Every game can receive a prediction, but only rows passing this gate are actionable picks.",
    }
    tags = list(out.get("tags") or [])
    tags.append("ACTIONABLE_PICK" if actionable else "NO_PICK")
    tags.append("CALIBRATED_PROBABILITY")
    out["tags"] = sorted(set(tags))
    return out


def optimize_with_fundamentals(row: Dict[str, Any]) -> Dict[str, Any]:
    out = _apply_fundamentals(row)
    out = _calibrate(out)
    out = _no_pick(out)
    return out


def apply(module):
    if getattr(module, "_INQSI_MLB_FUNDAMENTALS_OPTIMIZER_APPLIED", False):
        return module
    original_predict_all = module.predict_all

    def patched_predict_all(*args, **kwargs):
        result = original_predict_all(*args, **kwargs)
        if not isinstance(result, dict):
            return result
        predictions = [optimize_with_fundamentals(row) for row in (result.get("predictions") or [])]
        predictions.sort(key=lambda r: (float(r.get("actionablePick") is True), float(r.get("score") or 0), float(r.get("winProbability") or 0)), reverse=True)
        for idx, row in enumerate(predictions, 1):
            row["rank"] = idx
        result["predictions"] = predictions
        result["count"] = len(predictions)
        summary = dict(result.get("rolling24hAccuracyTarget") or result.get("accuracyTarget") or {})
        summary["fundamentalsEnabled"] = USE_FUNDAMENTALS
        summary["fundamentalsMode"] = "SPORTSDATAIO_ENABLED_WHEN_AVAILABLE" if USE_FUNDAMENTALS else "DISABLED_ODDS_API_ONLY"
        summary["fundamentalsAppliedCount"] = len([r for r in predictions if (r.get("winnerOptimizer") or {}).get("fundamentalsApplied")])
        summary["fundamentalsMissingCount"] = len([r for r in predictions if "MISSING_FUNDAMENTALS" in set(r.get("tags") or [])])
        summary["fundamentalsFlipCount"] = len([r for r in predictions if r.get("optimizerFlippedByFundamentals")])
        summary["calibrationEnabled"] = CALIBRATION_ENABLED
        summary["calibratedPredictionCount"] = len([r for r in predictions if (r.get("calibration") or {}).get("enabled")])
        summary["noPickDisciplineEnabled"] = NO_PICK_ENABLED
        summary["actionablePickCount"] = len([r for r in predictions if r.get("actionablePick")])
        summary["noPickCount"] = len([r for r in predictions if not r.get("actionablePick")])
        summary["patchVersion"] = PATCH_VERSION
        summary["fundamentalsCachePolicy"] = "slate_date_keyed_cache"
        result["rolling24hAccuracyTarget"] = summary
        result["accuracyTarget"] = summary
        result["actionablePickCount"] = summary["actionablePickCount"]
        result["noPickCount"] = summary["noPickCount"]
        result["calibrationPolicy"] = {
            "enabled": CALIBRATION_ENABLED,
            "method": "market_consensus_score_blend_with_risk_shrinkage",
            "brierLogLossReady": True,
            "note": "Probabilities are compressed toward market consensus and penalized for instability before the no-pick gate is applied.",
        }
        result["noPickPolicy"] = {
            "enabled": NO_PICK_ENABLED,
            "minPulls": MIN_ACTIONABLE_PULLS,
            "minCalibratedProbability": MIN_ACTIONABLE_PROB,
            "minScore": MIN_ACTIONABLE_SCORE,
            "maxRiskPenalty": MAX_ACTIONABLE_RISK,
        }
        suffix = "+fundamentals-calibration-no-pick-v1"
        model = str(result.get("modelVersion") or "")
        result["modelVersion"] = model if suffix in model else model + suffix
        return result

    module.predict_all = patched_predict_all
    module._INQSI_MLB_FUNDAMENTALS_OPTIMIZER_APPLIED = True
    return module
