from __future__ import annotations

from typing import Any, Dict, List

VERSION = "MLB-DIRECTIONAL-SCORE-v2-premium-clean-official-gate"
HARD_BLOCK_TAGS = {
    "BOOK_DIVERGENCE",
    "COMPRESSED_MARKET",
    "UNCONFIRMED_RUN_LINE_MOVE",
    "LATE_INSTABILITY",
    "RESISTANCE",
    "LOW_PULL_DEPTH",
    "SINGLE_PULL_BASELINE",
}


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
    pull_depth = _i(r.get("pullCountForGame"), _i(s.get("pullCount"), 0))
    move_abs = abs(d)
    book_agreement = "BOOK_AGREEMENT" in t and "BOOK_DIVERGENCE" not in t
    run_aligned = (run < 0 and d > 0) or (run > 0 and d < 0)
    steam_aligned = "STEAM" in t and d > 0 and edge > 0.05
    clean_confirmation = bool(
        book_agreement
        and rev <= 1
        and edge >= 0.10
        and d >= 0
        and not (t & HARD_BLOCK_TAGS)
        and (rev == 0 or steam_aligned or ("RUN_LINE_CONFIRMATION" in t and run_aligned))
    )
    return {
        "netMove": round(d, 6),
        "edge": round(edge, 6),
        "gap": round(gap, 6),
        "probability": round(p, 6),
        "opponentProbability": round(op, 6),
        "reversals": rev,
        "movePerReversal": round(move_abs / max(1, rev), 6),
        "opponentFavored": edge < 0,
        "compressed": bool("COMPRESSED_MARKET" in t or gap < 0.05),
        "narrow": 0.05 <= gap < 0.10,
        "strongMove": move_abs >= 0.10,
        "decisiveMove": move_abs >= 0.20,
        "tinyMove": move_abs < 0.015,
        "highReversalDirectional": rev >= 4 and move_abs >= 0.10 and edge > 0.05,
        "highReversalWeak": rev >= 3 and (edge <= 0.10 or gap < 0.10 or move_abs < 0.04),
        "runAligned": run_aligned,
        "steamAligned": steam_aligned,
        "bookAgreement": book_agreement,
        "cleanConfirmation": clean_confirmation,
        "missingFundamentals": "MISSING_FUNDAMENTALS" in t,
        "lowDepth": "LOW_PULL_DEPTH" in t or "SINGLE_PULL_BASELINE" in t or (0 < pull_depth < 12),
        "favoriteRisk": p >= 0.68 and d < 0.025 and move_abs < 0.20,
        "hardBlockTags": sorted(t & HARD_BLOCK_TAGS),
    }


def _components(r: Dict[str, Any]) -> List[Dict[str, Any]]:
    f = _features(r)
    t = _tags(r, _sig(r))
    tier = str(r.get("confidenceTier") or "").lower()
    out: List[Dict[str, Any]] = []

    def add(n: str, v: float):
        out.append({"name": n, "value": round(v, 2)})

    if tier == "premium":
        add("premium", 4)
    elif tier == "solid":
        add("solid", 1)
    elif tier == "lean":
        add("lean", -4)
    elif "coin" in tier:
        add("coin_flip", -6)
    elif tier in {"pass", "baseline"}:
        add("pass_or_baseline", -10)

    if f["opponentFavored"]:
        add("opponent_favored", -12)
    elif f["edge"] >= 0.25:
        add("strong_edge", 7)
    elif f["edge"] >= 0.20:
        add("official_edge", 5)
    elif f["edge"] >= 0.10:
        add("usable_edge", 2)
    else:
        add("weak_edge", -7)

    if f["decisiveMove"] and f["edge"] > 0:
        add("decisive_move", 7)
    elif f["strongMove"] and f["edge"] > 0:
        add("strong_move", 4)
    elif f["tinyMove"]:
        add("tiny_move", -5)
    elif f["netMove"] < 0:
        add("move_against", -7)

    if f["reversals"] == 0:
        add("zero_reversal", 4)
    elif f["reversals"] == 1:
        add("one_reversal_caution", 0)
    elif f["reversals"] == 2:
        add("two_reversal_penalty", -6)
    elif 3 <= f["reversals"] <= 4:
        add("high_reversal_penalty", -12)
    else:
        add("very_high_reversal_penalty", -16)

    if f["movePerReversal"] >= 0.04 and f["edge"] > 0 and f["reversals"] <= 1:
        add("clean_move_per_reversal", 3)
    if f["compressed"]:
        add("compressed", -12)
    elif f["narrow"]:
        add("narrow", -5)

    if "RUN_LINE_MOVEMENT" in t and f["runAligned"] and f["edge"] >= 0.10 and f["reversals"] <= 1:
        add("runline_confirmed", 3)
    elif "RUN_LINE_MOVEMENT" in t:
        add("runline_noise", -4)
    if "STEAM" in t and f["steamAligned"] and f["reversals"] <= 1:
        add("steam_confirmed", 3)
    elif "STEAM" in t:
        add("steam_noise", -4)
    if f["missingFundamentals"] and f["edge"] < 0.25:
        add("missing_fundamentals", -5)
    if f["lowDepth"]:
        add("low_depth", -8)
    if f["favoriteRisk"]:
        add("favorite_risk", -8)
    if f["hardBlockTags"]:
        add("hard_block_profile", -15)
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
        (f["reversals"] >= 2, 55.0, "multiple_reversals"),
        (str(r.get("confidenceTier") or "").lower() in {"pass", "baseline"}, 49.0, "pass_or_baseline"),
        (f["favoriteRisk"], 62.0, "favorite_risk"),
        (bool(f["hardBlockTags"]), 50.0, "hard_block_profile"),
    ]:
        if cond:
            cap = min(cap, c)
            reasons.append(name)
    return {"raw": raw, "cap": cap, "capped": round(min(raw, cap), 2), "reasons": reasons, "score": round(score, 2)}


def _base_official(r: Dict[str, Any]) -> bool:
    return bool(r.get("officialPick") is True or r.get("actionablePick") is True or r.get("accuracyTargetEligible") is True)


def _official(r: Dict[str, Any], score: float, cap: Dict[str, Any]) -> tuple[bool, List[str]]:
    f = _features(r)
    tier = str(r.get("confidenceTier") or "").lower()
    reasons: List[str] = []
    if not _base_official(r):
        reasons.append("not_promoted_by_base_signal_gate")
    if tier != "premium":
        reasons.append("premium_tier_required")
    if score < 72:
        reasons.append("directional_score_below_72")
    if cap["capped"] < 70:
        reasons.append("capped_probability_below_70")
    if f["probability"] < 0.62:
        reasons.append("market_probability_below_62pct")
    if f["edge"] < 0.20:
        reasons.append("market_edge_below_20pts")
    if not f["bookAgreement"]:
        reasons.append("book_agreement_required")
    if f["reversals"] > 1:
        reasons.append("multiple_reversals_block")
    if f["netMove"] < 0:
        reasons.append("movement_against_selected_side")
    if f["reversals"] == 1 and not f["cleanConfirmation"]:
        reasons.append("single_reversal_requires_clean_confirmation")
    if f["hardBlockTags"]:
        reasons.append("hard_block_signal_present")
    if f["opponentFavored"] or f["compressed"] or f["lowDepth"] or f["favoriteRisk"]:
        reasons.append("directional_risk_profile_block")
    if f["missingFundamentals"] and not (f["edge"] >= 0.25 and f["probability"] >= 0.625):
        reasons.append("missing_fundamentals_requires_stronger_market")
    return not reasons, sorted(set(reasons))


def _quality(r: Dict[str, Any], score: float, official: bool) -> str:
    f = _features(r)
    if official:
        return "Official Pick"
    if score >= 72 and f["edge"] > 0.10:
        return "Strong Prediction"
    if score >= 58 and not f["opponentFavored"]:
        return "Lean Prediction"
    if score >= 45 and not f["compressed"]:
        return "Coin Flip Prediction"
    return "Pass / Forced Prediction"


def _apply_row(r: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(r or {})
    base = _f(out.get("scoreAfterSignalPolicyV13"), _f(out.get("score"), 0.0))
    comps = _components(out)
    adj = round(sum(_f(c.get("value")) for c in comps), 2)
    score = max(0.0, min(100.0, base + adj))
    cap = _cap(out, score)
    official, gate_reasons = _official(out, score, cap)
    out["directionalScoreV1"] = {
        "applied": True,
        "version": VERSION,
        "blocksPrediction": False,
        "features": _features(out),
        "components": comps,
        "adjustment": adj,
        "probabilityCap": cap,
        "officialAllowed": official,
        "officialGateReasons": gate_reasons,
        "officialPolicy": "premium_clean_market_only; validated ML may promote after this gate",
    }
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
        out["actionability"] = "ACTIONABLE_PREMIUM_CLEAN_WINNER_SELECTION"
        out["actionabilityReason"] = "premium_clean_directional_gate_passed"
    else:
        out["actionability"] = "LOW_CONFIDENCE_DIRECTIONAL_GATE_NOT_PLAYABLE"
        out["actionabilityReason"] = "directional_official_gate_failed_but_visible_prediction_preserved"
        risks = list(out.get("actionabilityRiskReasons") or []) + gate_reasons
        out["actionabilityRiskReasons"] = sorted(set(risks))
    tags = set(out.get("tags") or [])
    tags.add("DIRECTIONAL_SCORE_V2")
    tags.add("PREDICTION_REMAINS_AVAILABLE")
    tags.add(out["choiceQuality"].upper().replace(" ", "_").replace("/", ""))
    if not official:
        tags.add("NON_OFFICIAL_PREDICTION_DISPLAY")
        tags.add("NOT_PLAYABLE")
        tags.discard("ACTIONABLE_PICK")
    else:
        tags.add("ACTIONABLE_PICK")
        tags.discard("NOT_PLAYABLE")
    out["tags"] = sorted(tags)
    return out


def _card(r: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "gameId": r.get("gameId"),
        "gameKey": r.get("gameKey"),
        "homeTeam": r.get("homeTeam"),
        "awayTeam": r.get("awayTeam"),
        "commenceTime": r.get("commenceTime"),
        "predictedWinner": r.get("predictedWinner"),
        "confidenceTier": r.get("confidenceTier"),
        "choiceQuality": r.get("choiceQuality"),
        "winProbabilityPct": r.get("winProbabilityPct"),
        "cappedWinProbabilityPct": r.get("cappedWinProbabilityPct"),
        "scoreAfterDirectionalV1": r.get("scoreAfterDirectionalV1"),
        "displayGroup": r.get("displayGroup"),
        "isOfficial": bool(r.get("isOfficialDisplayPick")),
        "riskReasons": r.get("actionabilityRiskReasons") or [],
    }


def enhance_result(result: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return result
    rows = [_apply_row(x) for x in (result.get("predictions") or []) if isinstance(x, dict)]
    official = [_card(x) for x in rows if x.get("isOfficialDisplayPick")]
    non = [_card(x) for x in rows if not x.get("isOfficialDisplayPick")]
    out = dict(result)
    out["predictions"] = rows
    out["officialPredictionDisplay"] = official
    out["nonOfficialPredictionDisplay"] = non
    out["actionablePickCount"] = len(official)
    out["noPickCount"] = len(non)
    out["directionalScoreV1"] = {
        "applied": True,
        "version": VERSION,
        "blocksPrediction": False,
        "officialDisplayCount": len(official),
        "nonOfficialDisplayCount": len(non),
        "officialPolicy": "premium clean market only; no forced official selection",
        "features": [
            "netMove",
            "movePerReversal",
            "edge",
            "opponentFavored",
            "compressed",
            "favoriteRisk",
            "runAligned",
            "steamAligned",
            "cleanConfirmation",
            "probabilityCap",
            "choiceQuality",
        ],
    }
    if VERSION not in str(out.get("modelVersion") or ""):
        out["modelVersion"] = str(out.get("modelVersion") or "") + "+" + VERSION
    return out


def apply(module: Any):
    if getattr(module, "_INQSI_MLB_DIRECTIONAL_SCORE_V1_APPLIED", False):
        return module
    original = module.predict_all

    def patched_predict_all(*args, **kwargs):
        return enhance_result(original(*args, **kwargs))

    module.predict_all = patched_predict_all
    module._INQSI_MLB_DIRECTIONAL_SCORE_V1_APPLIED = True
    return module
