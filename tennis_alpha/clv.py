from __future__ import annotations

from typing import Any, Mapping

from features import implied_american


def fair_pair(player_odds: float, opponent_odds: float) -> float:
    p1 = implied_american(float(player_odds))
    p2 = implied_american(float(opponent_odds))
    denom = p1 + p2
    return p1 / denom if denom else 0.5


def clv_report(
    model_p: float,
    open_player_odds: float,
    open_opp_odds: float,
    close_player_odds: float,
    close_opp_odds: float,
    player_won: bool,
) -> dict[str, Any]:
    open_p = fair_pair(open_player_odds, open_opp_odds)
    close_p = fair_pair(close_player_odds, close_opp_odds)
    label = 1.0 if player_won else 0.0
    return {
        "model_p": model_p,
        "open_fair_p": open_p,
        "close_fair_p": close_p,
        "clv": model_p - close_p,
        "line_move": close_p - open_p,
        "beat_close": abs(model_p - label) < abs(close_p - label),
        "player_won": player_won,
    }


def from_snaps(model_p: float, first: Mapping[str, Any], last: Mapping[str, Any], player_won: bool) -> dict[str, Any]:
    return clv_report(
        model_p,
        float(first["player_odds"]),
        float(first["opponent_odds"]),
        float(last["player_odds"]),
        float(last["opponent_odds"]),
        player_won,
    )
