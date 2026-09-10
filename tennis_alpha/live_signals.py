from __future__ import annotations

from typing import Any

from names import match_name
from ratings import RatingStore


def live_signals(
    store: RatingStore,
    player: str,
    opponent: str,
    player_odds: float,
    opponent_odds: float,
    surface: str,
    best_of_five: bool,
    date: int = 0,
) -> dict[str, Any]:
    catalog = store.names()
    p_name = match_name(player, catalog) or player
    o_name = match_name(opponent, catalog) or opponent
    pre = store.pre_match(p_name, o_name, surface, date)
    return {
        "player_odds": player_odds,
        "opponent_odds": opponent_odds,
        "elo_diff": pre["elo_diff"],
        "surface_elo_diff": pre["surface_elo_diff"],
        "rank_points_edge": 0.0,
        "recent_surface_wr_diff": pre["recent_surface_wr_diff"],
        "h2h_edge": pre["h2h_edge"],
        "serve_points_won_diff": 0.0,
        "return_points_won_diff": 0.0,
        "break_points_saved_diff": 0.0,
        "rest_days_diff": 0.0,
        "best_of_five": best_of_five,
        "matched_player": p_name,
        "matched_opponent": o_name,
        "surface": surface,
    }
