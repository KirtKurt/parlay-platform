from __future__ import annotations

from typing import Any, Dict, Mapping, Tuple

FEATURE_NAMES: Tuple[str, ...] = (
    "market_fair_prob",
    "elo_diff_scaled",
    "surface_elo_diff_scaled",
    "rank_points_edge",
    "recent_surface_wr_diff",
    "h2h_edge",
    "serve_points_won_diff",
    "return_points_won_diff",
    "break_points_saved_diff",
    "rest_advantage_scaled",
    "best_of_five",
)


def implied_american(odds: float) -> float:
    if odds == 0:
        raise ValueError("american odds cannot be zero")
    return abs(odds) / (abs(odds) + 100.0) if odds < 0 else 100.0 / (odds + 100.0)


def clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def build(signals: Mapping[str, Any]) -> Dict[str, float]:
    if "market_fair_prob" in signals:
        market = clip(float(signals["market_fair_prob"]), 0.02, 0.98)
    elif "player_odds" in signals and "opponent_odds" in signals:
        p1 = implied_american(float(signals["player_odds"]))
        p2 = implied_american(float(signals["opponent_odds"]))
        denom = p1 + p2
        market = p1 / denom if denom else 0.5
    else:
        market = 0.5
    return {
        "market_fair_prob": market,
        "elo_diff_scaled": clip(float(signals.get("elo_diff", 0)) / 400.0, -3.0, 3.0),
        "surface_elo_diff_scaled": clip(float(signals.get("surface_elo_diff", 0)) / 400.0, -3.0, 3.0),
        "rank_points_edge": clip(float(signals.get("rank_points_edge", 0)) / 4000.0, -2.0, 2.0),
        "recent_surface_wr_diff": clip(float(signals.get("recent_surface_wr_diff", 0)), -1.0, 1.0),
        "h2h_edge": clip(float(signals.get("h2h_edge", 0)), -1.0, 1.0),
        "serve_points_won_diff": clip(float(signals.get("serve_points_won_diff", 0)), -1.0, 1.0),
        "return_points_won_diff": clip(float(signals.get("return_points_won_diff", 0)), -1.0, 1.0),
        "break_points_saved_diff": clip(float(signals.get("break_points_saved_diff", 0)), -1.0, 1.0),
        "rest_advantage_scaled": clip(float(signals.get("rest_days_diff", 0)) / 7.0, -2.0, 2.0),
        "best_of_five": 1.0 if signals.get("best_of_five") else 0.0,
    }


def vector(feat: Mapping[str, float]) -> list[float]:
    return [float(feat[name]) for name in FEATURE_NAMES]
