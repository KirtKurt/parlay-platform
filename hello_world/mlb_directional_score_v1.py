from __future__ import annotations

from typing import Any, Dict, List

VERSION = "MLB-DIRECTIONAL-SCORE-v1"


def _f(v: Any, d: float = 0.0) -> float:
    try:
        return d if v is None or v == "" else float(v)
    except Exception:
        return d


def _i(v: Any, d: int = 0) -> int:
    try:
        return int(round(float(v)))
    except Exception:
        return d


def _side(r: Dict[str, Any]) -> str:
    s = str(r.get("predictedSide") or "").lower()
    return s if s in {"home", "away"} else "home"


def _sig(r: Dict[str, Any]) -> Dict[str, Any]:
    v = r.get("homeSignal") if _side(r) == "home" else r.get("awaySignal")
    return v if isinstance(v, dict) else {}


def _opp(r: Dict[str, Any]) -> Dict[str, Any]:
    v = r.get("awaySignal") if _side(r) == "home" else r.get("homeSignal")
    return v if isinstance(v, dict) else {}


def _tags(r: Dict[str, Any], s: Dict[str, Any]) -> set[str]:
    return set([str(x) for x in (r.get("tags") or [])] + [str(x) for x in (s.get("tags") or [])])


def _features(r: Dict[str, Any]) -> Dict[str, Any]:
    s, o = _sig(r), _opp(r)
    p = _f(s.get("marketConsensusProbability"), _f(s.get("probLatest"), 0.5))
    op = _f(o.get("marketConsensusProbability"), _f(o.get("probLatest"), 1.0 - p))
    ps = _f(s.get("probStart"), p)
    d = _f(s.get("delta"), p - ps)
    edge = p - op
    gap = _f(s.get("latestGap"), abs(edge))
    rev = _i(s.get("reversalCount"), 0)
    run = _f(s.get("runLineMovement"), 0.0)
    t = _tags(r, s)
    pull_depth = _i(r.get("pullCountForGame"), 0)
    move_abs = abs(d)
    return {
        "netMove": round(d, 6),
        "edge": round(edge, 6),
        "gap": round(gap, 6),
        "probability": round(p, 6),
        "opponentProbability": round(op, 6),
        "reversals": rev,
        "movePerReversal": round(move_abs / max(1, rev), 6),
        "opponentFavored": edge < 0,
        "compressed": gap < 0.05,
        "narrow": 0.05 <= gap < 0.10,
        "strongMove": move_abs >= 0.10,
        "decisiveMove": move_abs >= 0.20,
        "tinyMove": move_abs < 0.015,
        "highReversalDirectional": rev >= 4 and move_abs >= 0.10 and edge > 0.05,
        "highReversalWeak": rev >= 4 and (edge <= 0.05 or gap < 0.05 or move_abs < 0.015),
        "runAligned": (run < 0 and d > 0) or (run > 0 and d < 0),
        "steamAligned": "STEAM" in t and d > 0 and edge > 0.05,
        "missingFundamentals": "MISSING_FUNDAMENTALS" in t,
        "lowDepth": "LOW_PULL_DEPTH" in t or pull_depth < 12,
        "favoriteRisk": p >= 0.68 and d < 0.025 and move_abs < 0.20,
    }


def _components(r: Dict[str, Any]) -> List[Dict[str, Any]]:
    f = _features(r)
    t = _tags(r, _sig(r))
    tier = str(r.get("confidenceTier") or "").lower()
    out: List[Dict[str, Any]] = []
    def add(n: str, v: float):
        out.append({"name": n, "value": round(v, 2)})
    if tier == "premium": add("premium", 3)
    elif tier == "solid": add("solid", 2)
    elif tier == "lean": add("lean", -3)
    elif "coin" in tier: add("coin_flip", -4)
    elif tier == "pass": add("pass", -7)
    if f["opponentFavored"]: add("opponent_favored", -10)
    elif f["edge"] >= 0.25: add("strong_edge", 6)
    elif f["edge"] >= 0.10: add("usable_edge", 3)
    elif f["edge"] < 0.05: add("weak_edge", -5)
    if f["decisiveMove"] and f["edge"] > 0: add("decisive_move", 8)
    elif f["strongMove"] and f["edge"] > 0: add("strong_move", 5)
    elif f["tinyMove"]: add("tiny_move", -5)
    elif f["netMove"] < -0.01: add("move_against", -6)
    if f["highReversalDirectional"]: add("reversal_directional_override", 5)
    elif f["highReversalWeak"]: add("reversal_weak_edge", -9)
    elif f["reversals"] <= 2: add("low_reversal", 2)
    elif f["reversals"] == 3: add("three_reversal", -2)
    if f["movePerReversal"] >= 0.04 and f["edge"] > 0: add("move_per_reversal", 4)
    if f["compressed"]: add("compressed", -9)
    elif f["narrow"]: add("narrow", -4)
    if "RUN_LINE_MOVEMENT" in t and f["runAligned"] and f["edge"] >= 0.10: add("runline_confirmed", 3)
    elif "RUN_LINE_MOVEMENT" in t: add("runline_noise", -3)
    if "STEAM" in t and f["steamAligned"]: add("steam_confirmed", 3)
    elif "STEAM" in t: add("steam_noise", -3)
    if f["missingFundamentals"] and f["edge"] < 0.25: add("missing_fundamentals", -4)
    if f["lowDepth"]: add("low_depth", -5)
    if f["favoriteRisk"]: add("favorite_risk", -7)
    return out


def _cap(r: Dict[str, Any], score: float) -> Dict[str, Any]:
    f = _features(r)
    raw = _f(r.get("winProbabilityPct"), 50.0)
    cap = 95.0
    reasons: List[str] = []
    for cond, c, name in [
        (f["lowDepth"], 55.0, "low_depth"),
        (f["compressed"], 52.0, "compressed"),
        (f["narrow"], 58.0, "narrow"),
        (f["opponentFavored"], 48.0, "opponent_favored"),
        (f["missingFundamentals"] and f["edge"] < 0.25, 64.0, "missing_fundamentals"),
        (f["highReversalWeak"], 55.0, "reversal_weak_edge"),
        (str(r.get("confidenceTier") or "").lower() == "pass", 49.0, "pass"),
        (f["favoriteRisk"], 72.0, "favorite_risk"),
    ]:
        if cond:
            cap = min(cap, c); reasons.append(name)
    return {"raw": raw, "cap": cap, "capped": round(min(raw, cap), 2), "reasons": reasons, "score": round(score, 2)}


def _base_official(r: Dict[str, Any]) -> bool:
    return bool(r.get("officialPick") is True or r.get("actionablePick") is True or r.get("accuracyTargetEligible") is True)


def _official(r: Dict[str, Any], score: float, cap: Dict[str, Any]) -> bool:
    f = _features(r)
    if not _base_official(r): return False
    if f["opponentFavored"] or f["compressed"] or f["lowDepth"] or f["favoriteRisk"]: return False
    if score < 62 or cap["capped"] < 59: return False
    if f["missingFundamentals"] and not (f["edge"] >= 0.20 and f["probability"] >= 0.60): return False
    if f["reversals"] >= 4 and not (f["strongMove"] and f["edge"] > 0.05): return False
    return True


def _quality(r: Dict[str, Any], score: float, official: bool) -> str:
    f = _features(r)
    if official: return "Official Pick"
    if score >= 72 and f["edge"] > 0.10: return "Strong Prediction"
    if score >= 58 and not f["opponentFavored"]: return "Lean Prediction"
    if score >= 45 and not f["compressed"]: return "Coin Flip Prediction"
    return "Pass / Forced Prediction"


def _apply_row(r: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(r or {})
    base = _f(out.get("scoreAfterSignalPolicyV13"), _f(out.get("score"), 0.0))
    comps = _components(out)
    adj = round(sum(_f(c.get("value")) for c in comps), 2)
    score = max(0.0, min(100.0, base + adj))
    cap = _cap(out, score)
    official = _official(out, score, cap)
    out["directionalScoreV1"] = {"applied": True, "version": VERSION, "blocksPrediction": False, "features": _features(out), "components": comps, "adjustment": adj, "probabilityCap": cap, "officialAllowed": official}
    out["scoreBeforeDirectionalV1"] = round(base, 2)
    out["directionalScoreV1Adjustment"] = adj
    out["scoreAfterDirectionalV1"] = round(score, 2)
    out["cappedWinProbabilityPct"] = cap["capped"]
    out["choiceQuality"] = _quality(out, score, official)
    out["predictionRemainsAvailable"] = True
    out["isOfficialDisplayPick"] = official
    out["displayGroup"] = "official" if official else "non_official_prediction"
    out["officialPick"] = official
    out["actionablePick"] = official
    out["accuracyTargetEligible"] = official
    if official:
        out["actionability"] = "ACTIONABLE_WINNER_SELECTION"
        out["actionabilityReason"] = "directional_score_official_gate_passed"
    tags = set(out.get("tags") or [])
    tags.add("DIRECTIONAL_SCORE_V1")
    tags.add("PREDICTION_REMAINS_AVAILABLE")
    tags.add(out["choiceQuality"].upper().replace(" ", "_").replace("/", ""))
    if not official:
        tags.add("NON_OFFICIAL_PREDICTION_DISPLAY")
        tags.discard("ACTIONABLE_PICK")
    else:
        tags.add("ACTIONABLE_PICK")
    out["tags"] = sorted(tags)
    return out


def _card(r: Dict[str, Any]) -> Dict[str, Any]:
    return {"gameId": r.get("gameId"), "gameKey": r.get("gameKey"), "homeTeam": r.get("homeTeam"), "awayTeam": r.get("awayTeam"), "commenceTime": r.get("commenceTime"), "predictedWinner": r.get("predictedWinner"), "confidenceTier": r.get("confidenceTier"), "choiceQuality": r.get("choiceQuality"), "winProbabilityPct": r.get("winProbabilityPct"), "cappedWinProbabilityPct": r.get("cappedWinProbabilityPct"), "scoreAfterDirectionalV1": r.get("scoreAfterDirectionalV1"), "displayGroup": r.get("displayGroup"), "isOfficial": bool(r.get("isOfficialDisplayPick")), "riskReasons": r.get("actionabilityRiskReasons") or []}


def enhance_result(result: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(result, dict): return result
    rows = [_apply_row(x) for x in (result.get("predictions") or []) if isinstance(x, dict)]
    official = [_card(x) for x in rows if x.get("isOfficialDisplayPick")]
    non = [_card(x) for x in rows if not x.get("isOfficialDisplayPick")]
    out = dict(result)
    out["predictions"] = rows
    out["officialPredictionDisplay"] = official
    out["nonOfficialPredictionDisplay"] = non
    out["actionablePickCount"] = len(official)
    out["noPickCount"] = len(non)
    out["directionalScoreV1"] = {"applied": True, "version": VERSION, "blocksPrediction": False, "officialDisplayCount": len(official), "nonOfficialDisplayCount": len(non), "features": ["netMove", "movePerReversal", "edge", "opponentFavored", "compressed", "favoriteRisk", "runAligned", "steamAligned", "probabilityCap", "choiceQuality"]}
    if VERSION not in str(out.get("modelVersion") or ""):
        out["modelVersion"] = str(out.get("modelVersion") or "") + "+" + VERSION
    return out


def apply(module: Any):
    if getattr(module, "_INQSI_MLB_DIRECTIONAL_SCORE_V1_APPLIED", False): return module
    original = module.predict_all
    def patched_predict_all(*args, **kwargs):
        return enhance_result(original(*args, **kwargs))
    module.predict_all = patched_predict_all
    module._INQSI_MLB_DIRECTIONAL_SCORE_V1_APPLIED = True
    return module
