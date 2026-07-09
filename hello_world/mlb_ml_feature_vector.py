from __future__ import annotations

from typing import Any, Dict, List

VERSION = "MLB-ML-FEATURE-VECTOR-v3-underdog-balanced-directional"

ML_FEATURES: List[str] = [
    "score",
    "winProbabilityPct",
    "marketProb",
    "marketEdge",
    "marketDelta",
    "marketGap",
    "bookDivergence",
    "reversalCount",
    "movementPerReversal",
    "runLineMoveAbs",
    "bookAgreement",
    "bookDivergenceFlag",
    "runLineMove",
    "runLineAligned",
    "unconfirmedRunLine",
    "steam",
    "steamAligned",
    "resistance",
    "compressedMarket",
    "missingFundamentals",
    "lowPullDepth",
    "favoriteRisk",
    "opponentFavored",
    "highReversalDirectional",
    "highReversalWeak",
    "selectedUnderdog",
    "selectedFavorite",
    "underdogPositiveMove",
    "underdogEdgeImproving",
    "favoriteFlatMoveRisk",
    "favoriteCompressedRisk",
    "lean",
    "passTier",
]


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _i(value: Any, default: int = 0) -> int:
    try:
        return int(round(float(value)))
    except Exception:
        return default


def _side(row: Dict[str, Any]) -> str:
    side = str(row.get("predictedSide") or "").lower()
    return side if side in {"home", "away"} else "home"


def _signal(row: Dict[str, Any], side: str) -> Dict[str, Any]:
    sig = row.get("homeSignal") if side == "home" else row.get("awaySignal")
    return sig if isinstance(sig, dict) else {}


def _tags(row: Dict[str, Any], sig: Dict[str, Any]) -> set[str]:
    return set([str(x) for x in (row.get("tags") or [])] + [str(x) for x in (sig.get("tags") or [])])


def feature_vector(row: Dict[str, Any]) -> Dict[str, float]:
    side = _side(row)
    other = "away" if side == "home" else "home"
    sig = _signal(row, side)
    opp = _signal(row, other)
    tags = _tags(row, sig)
    tier = str(row.get("confidenceTier") or "").lower()

    market_prob = _f(sig.get("marketConsensusProbability"), _f(sig.get("probLatest"), 0.5))
    opp_prob = _f(opp.get("marketConsensusProbability"), _f(opp.get("probLatest"), 1.0 - market_prob))
    market_edge = market_prob - opp_prob
    prob_start = _f(sig.get("probStart"), market_prob)
    market_delta = _f(sig.get("delta"), market_prob - prob_start)
    market_gap = _f(sig.get("latestGap"), abs(market_edge))
    reversal_count = _f(sig.get("reversalCount"), 0.0)
    movement_abs = abs(market_delta)
    movement_per_reversal = movement_abs / max(1.0, reversal_count)
    run_line_move = _f(sig.get("runLineMovement"), 0.0)
    run_line_abs = abs(run_line_move)
    pull_depth = _i(row.get("pullCountForGame"), _i(sig.get("pullCount"), 0))
    avg_american_odds = _f(sig.get("averageAmericanOdds"), 0.0)

    run_line_aligned = bool((run_line_move < 0 and market_delta > 0) or (run_line_move > 0 and market_delta < 0))
    steam = "STEAM" in tags
    steam_aligned = bool(steam and market_delta > 0 and market_edge > 0.05)
    compressed = bool("COMPRESSED_MARKET" in tags or market_gap < 0.05)
    missing_fundamentals = bool("MISSING_FUNDAMENTALS" in tags)
    low_pull_depth = bool("LOW_PULL_DEPTH" in tags or 0 < pull_depth < 12)
    favorite_risk = bool(market_prob >= 0.68 and market_delta < 0.025 and movement_abs < 0.20)
    high_reversal_directional = bool(reversal_count >= 4 and movement_abs >= 0.10 and market_edge > 0.05)
    high_reversal_weak = bool(reversal_count >= 4 and (market_edge <= 0.05 or compressed or movement_abs < 0.015))

    selected_underdog = bool(avg_american_odds > 0 or market_prob < 0.50)
    selected_favorite = bool(avg_american_odds < 0 and market_prob >= 0.50)
    underdog_positive_move = bool(selected_underdog and market_delta > 0.02)
    underdog_edge_improving = bool(selected_underdog and market_delta > 0.035 and market_edge >= -0.06 and not high_reversal_weak)
    favorite_flat_move_risk = bool(selected_favorite and market_prob >= 0.58 and movement_abs < 0.015)
    favorite_compressed_risk = bool(selected_favorite and compressed and market_edge < 0.08)

    return {
        "score": _f(row.get("score")),
        "winProbabilityPct": _f(row.get("winProbabilityPct")),
        "marketProb": market_prob,
        "marketEdge": market_edge,
        "marketDelta": market_delta,
        "marketGap": market_gap,
        "bookDivergence": _f(sig.get("bookDivergence")),
        "reversalCount": reversal_count,
        "movementPerReversal": movement_per_reversal,
        "runLineMoveAbs": run_line_abs,
        "bookAgreement": 1.0 if "BOOK_AGREEMENT" in tags else 0.0,
        "bookDivergenceFlag": 1.0 if "BOOK_DIVERGENCE" in tags else 0.0,
        "runLineMove": 1.0 if "RUN_LINE_MOVEMENT" in tags else 0.0,
        "runLineAligned": 1.0 if run_line_aligned else 0.0,
        "unconfirmedRunLine": 1.0 if "UNCONFIRMED_RUN_LINE_MOVE" in tags else 0.0,
        "steam": 1.0 if steam else 0.0,
        "steamAligned": 1.0 if steam_aligned else 0.0,
        "resistance": 1.0 if "RESISTANCE" in tags else 0.0,
        "compressedMarket": 1.0 if compressed else 0.0,
        "missingFundamentals": 1.0 if missing_fundamentals else 0.0,
        "lowPullDepth": 1.0 if low_pull_depth else 0.0,
        "favoriteRisk": 1.0 if favorite_risk else 0.0,
        "opponentFavored": 1.0 if market_edge < 0 else 0.0,
        "highReversalDirectional": 1.0 if high_reversal_directional else 0.0,
        "highReversalWeak": 1.0 if high_reversal_weak else 0.0,
        "selectedUnderdog": 1.0 if selected_underdog else 0.0,
        "selectedFavorite": 1.0 if selected_favorite else 0.0,
        "underdogPositiveMove": 1.0 if underdog_positive_move else 0.0,
        "underdogEdgeImproving": 1.0 if underdog_edge_improving else 0.0,
        "favoriteFlatMoveRisk": 1.0 if favorite_flat_move_risk else 0.0,
        "favoriteCompressedRisk": 1.0 if favorite_compressed_risk else 0.0,
        "lean": 1.0 if tier == "lean" else 0.0,
        "passTier": 1.0 if tier == "pass" else 0.0,
    }
