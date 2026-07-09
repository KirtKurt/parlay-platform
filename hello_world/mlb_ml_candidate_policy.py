from __future__ import annotations

from typing import Any, Dict, List, Optional

VERSION = "MLB-ML-CANDIDATE-POLICY-v2-balanced-underdog"


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _on(value: Any) -> bool:
    return _f(value, 0.0) >= 0.5


def profiles() -> List[Dict[str, Any]]:
    return [
        {
            "name": "clean_market",
            "minMarketEdge": 0.08,
            "minMarketProb": 0.54,
            "minScore": 45.0,
            "minWinProbabilityPct": 52.0,
        },
        {
            "name": "directional_move",
            "minMarketEdge": 0.04,
            "minMarketProb": 0.53,
            "minMarketDelta": 0.015,
            "minMovementPerReversal": 0.01,
            "minScore": 42.0,
            "minWinProbabilityPct": 52.0,
        },
        {
            "name": "aligned_steam",
            "minMarketEdge": 0.04,
            "minMarketProb": 0.53,
            "requireSteamAligned": True,
            "minScore": 42.0,
            "minWinProbabilityPct": 52.0,
        },
        {
            "name": "underdog_positive_move",
            "requireSelectedUnderdog": True,
            "requireUnderdogPositiveMove": True,
            "minMarketEdge": -0.06,
            "minMarketDelta": 0.02,
            "minMovementPerReversal": 0.008,
            "minScore": 38.0,
            "minWinProbabilityPct": 48.0,
            "allowOpponentFavored": True,
        },
        {
            "name": "underdog_edge_improving",
            "requireSelectedUnderdog": True,
            "requireUnderdogEdgeImproving": True,
            "minMarketEdge": -0.08,
            "minMarketDelta": 0.035,
            "minScore": 35.0,
            "minWinProbabilityPct": 46.0,
            "allowOpponentFavored": True,
        },
    ]


def miss(features: Dict[str, Any], profile: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    checks = [
        ("marketEdge", "minMarketEdge", "market_edge_below_profile"),
        ("marketProb", "minMarketProb", "market_probability_below_profile"),
        ("marketDelta", "minMarketDelta", "market_delta_below_profile"),
        ("movementPerReversal", "minMovementPerReversal", "movement_per_reversal_below_profile"),
        ("score", "minScore", "score_below_profile"),
        ("winProbabilityPct", "minWinProbabilityPct", "win_probability_below_profile"),
    ]
    for feature, key, reason in checks:
        if key in profile and _f(features.get(feature), 0.0) < _f(profile.get(key), 0.0):
            out.append(reason)
    required_flags = [
        ("requireSteamAligned", "steamAligned", "steam_not_aligned"),
        ("requireSelectedUnderdog", "selectedUnderdog", "not_selected_underdog"),
        ("requireUnderdogPositiveMove", "underdogPositiveMove", "underdog_positive_move_missing"),
        ("requireUnderdogEdgeImproving", "underdogEdgeImproving", "underdog_edge_improving_missing"),
    ]
    for profile_key, feature, reason in required_flags:
        if profile.get(profile_key) and not _on(features.get(feature)):
            out.append(reason)
    hard_flags = [
        ("compressedMarket", "compressed_market"),
        ("highReversalWeak", "high_reversal_weak"),
        ("passTier", "pass_tier"),
        ("resistance", "resistance"),
        ("favoriteRisk", "favorite_risk"),
        ("favoriteCompressedRisk", "favorite_compressed_risk"),
        ("favoriteFlatMoveRisk", "favorite_flat_move_risk"),
    ]
    if not profile.get("allowOpponentFavored"):
        hard_flags.append(("opponentFavored", "opponent_favored"))
    for feature, reason in hard_flags:
        if _on(features.get(feature)):
            out.append(reason)
    return out


def ok(features: Dict[str, Any], profile: Dict[str, Any]) -> bool:
    return not miss(features, profile)


def choose(scored: List[Dict[str, Any]], target: float, base: float, min_selected: int = 2, low: float = 0.70) -> Optional[Dict[str, Any]]:
    hi = max(low, min(base - 0.01, 0.95))
    viable: List[Dict[str, Any]] = []
    for profile in profiles():
        for i in range(int(round(low * 100)), int(round(hi * 100)) + 1):
            threshold = i / 100.0
            selected = [row for row in scored if _f(row.get("p"), 0.0) >= threshold and ok(row.get("features") or {}, profile)]
            if len(selected) < min_selected:
                continue
            correct = [row for row in selected if int(row.get("label") or 0) == 1]
            acc = round(len(correct) / len(selected) * 100.0, 2)
            if acc >= target:
                viable.append({"threshold": threshold, "selectedCount": len(selected), "correct": len(correct), "accuracyPct": acc, "validated": True, "policyVersion": VERSION, "profile": profile})
    if not viable:
        return None
    return sorted(viable, key=lambda row: (row["selectedCount"], -row["threshold"], row["accuracyPct"]), reverse=True)[0]
