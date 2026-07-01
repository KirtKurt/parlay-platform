from __future__ import annotations

import math
import os
from typing import Any, Dict

try:
    import mlb_fundamentals_engine
except Exception:
    mlb_fundamentals_engine = None

# Odds API is the default operating mode. SportsDataIO can be re-enabled later by
# setting INQSI_MLB_USE_SPORTSDATAIO_FUNDAMENTALS=true after runtime proof passes.
USE_FUNDAMENTALS = os.environ.get("INQSI_MLB_USE_SPORTSDATAIO_FUNDAMENTALS", "false").lower() in {"1", "true", "yes"}
_FUNDAMENTALS_CACHE = None

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
        return float(value)
    except Exception:
        return default


def _norm(value: Any) -> str:
    return " ".join(str(value or "").lower().replace(".", "").strip().split())


def _candidates(value: Any) -> set[str]:
    base = _norm(value)
    out = {base, base.replace(" ", "")} if base else set()
    for alias in TEAM_ALIASES.get(base, []):
        out.add(_norm(alias))
    return {x for x in out if x}


def _prob(score: float) -> float:
    value = 1.0 / (1.0 + math.exp(-(score - 50.0) / 12.0))
    return max(0.05, min(0.95, value))


def _tier(prob: float, score: float, tags) -> str:
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


def _fundamentals_index() -> Dict[str, Dict[str, Any]]:
    global _FUNDAMENTALS_CACHE
    if _FUNDAMENTALS_CACHE is not None:
        return _FUNDAMENTALS_CACHE
    _FUNDAMENTALS_CACHE = {}
    if not USE_FUNDAMENTALS or mlb_fundamentals_engine is None:
        return _FUNDAMENTALS_CACHE
    try:
        preview = mlb_fundamentals_engine.slate_fundamentals_preview()
        if not isinstance(preview, dict) or not preview.get("ok"):
            return _FUNDAMENTALS_CACHE
        for game in preview.get("games") or []:
            for away in _candidates(game.get("awayTeam")):
                for home in _candidates(game.get("homeTeam")):
                    _FUNDAMENTALS_CACHE[f"{away}|{home}"] = game
    except Exception:
        _FUNDAMENTALS_CACHE = {}
    return _FUNDAMENTALS_CACHE


def _row_team(row: Dict[str, Any], side: str) -> Any:
    sig = row.get("homeSignal") if side == "home" else row.get("awaySignal")
    sig = sig or {}
    return row.get("homeTeam" if side == "home" else "awayTeam") or sig.get("team")


def _match(row: Dict[str, Any]) -> Dict[str, Any] | None:
    if not USE_FUNDAMENTALS:
        return None
    index = _fundamentals_index()
    for away in _candidates(_row_team(row, "away")):
        for home in _candidates(_row_team(row, "home")):
            found = index.get(f"{away}|{home}")
            if found:
                return found
    return None


def _apply_to_signal(sig: Dict[str, Any], adjustment: float) -> Dict[str, Any]:
    sig = dict(sig or {})
    adjustment = round(max(-8.0, min(8.0, adjustment)), 2)
    old_score = _num(sig.get("optimizedWinnerScore", sig.get("score")), 0.0)
    new_score = round(max(0.0, min(100.0, old_score + adjustment)), 2)
    p = _prob(new_score)
    sig["optimizedWinnerScoreBeforeFundamentals"] = old_score
    sig["fundamentalsAdjustment"] = adjustment
    sig["optimizedWinnerScore"] = new_score
    sig["score"] = new_score
    sig["winProbability"] = round(p, 4)
    sig["winProbabilityPct"] = round(p * 100.0, 2)
    return sig


def optimize_with_fundamentals(row: Dict[str, Any]) -> Dict[str, Any]:
    fundamentals = _match(row)
    if not fundamentals:
        out = dict(row)
        prior = dict(out.get("winnerOptimizer") or {})
        prior["fundamentalsApplied"] = False
        prior["fundamentalsMode"] = "DISABLED_ODDS_API_ONLY"
        prior["basis"] = prior.get("basis") or "market_signal_plus_multi_window_learning_odds_api_only"
        out["winnerOptimizer"] = prior
        return out
    home_adj = _num(((fundamentals.get("homeFundamentals") or {}).get("sideFundamentalsScore")), 0.0)
    away_adj = _num(((fundamentals.get("awayFundamentals") or {}).get("sideFundamentalsScore")), 0.0)
    home = _apply_to_signal(row.get("homeSignal") or {}, home_adj)
    away = _apply_to_signal(row.get("awaySignal") or {}, away_adj)
    pick = home if _num(home.get("optimizedWinnerScore"), -1.0) >= _num(away.get("optimizedWinnerScore"), -1.0) else away
    opponent = away if pick.get("side") == "home" else home
    score = _num(pick.get("optimizedWinnerScore"), 0.0)
    p = _num(pick.get("winProbability"), _prob(score))
    tags = sorted(set(pick.get("tags") or []))
    out = dict(row)
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
    prior = dict(out.get("winnerOptimizer") or {})
    prior["fundamentalsApplied"] = True
    prior["fundamentalsMode"] = "SPORTSDATAIO_ENABLED"
    prior["fundamentals"] = {
        "matchup": fundamentals.get("matchup"),
        "fundamentalsLean": fundamentals.get("fundamentalsLean"),
        "fundamentalsEdgeHomeMinusAway": fundamentals.get("fundamentalsEdgeHomeMinusAway"),
        "homeAdjustment": home.get("fundamentalsAdjustment"),
        "awayAdjustment": away.get("fundamentalsAdjustment"),
        "weights": "team_power_35pct_starter_45pct_bullpen_15pct_lineup_5pct",
    }
    prior["basis"] = "market_signal_plus_multi_window_learning_plus_sportsdataio_fundamentals"
    prior["homeOptimizedScore"] = home.get("optimizedWinnerScore")
    prior["awayOptimizedScore"] = away.get("optimizedWinnerScore")
    out["winnerOptimizer"] = prior
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
        predictions.sort(key=lambda r: (float(r.get("score") or 0), float(r.get("winProbability") or 0)), reverse=True)
        for idx, row in enumerate(predictions, 1):
            row["rank"] = idx
        result["predictions"] = predictions
        result["count"] = len(predictions)
        summary = dict(result.get("rolling24hAccuracyTarget") or result.get("accuracyTarget") or {})
        summary["fundamentalsEnabled"] = USE_FUNDAMENTALS
        summary["fundamentalsMode"] = "SPORTSDATAIO_ENABLED" if USE_FUNDAMENTALS else "ODDS_API_ONLY"
        summary["fundamentalsAppliedCount"] = len([r for r in predictions if (r.get("winnerOptimizer") or {}).get("fundamentalsApplied")])
        summary["fundamentalsFlipCount"] = len([r for r in predictions if r.get("optimizerFlippedByFundamentals")])
        result["rolling24hAccuracyTarget"] = summary
        result["accuracyTarget"] = summary
        suffix = "+sportsdataio-fundamentals" if USE_FUNDAMENTALS else "+odds-api-only"
        result["modelVersion"] = str(result.get("modelVersion") or "") + suffix
        return result

    module.predict_all = patched_predict_all
    module._INQSI_MLB_FUNDAMENTALS_OPTIMIZER_APPLIED = True
    return module
