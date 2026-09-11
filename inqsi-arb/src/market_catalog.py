"""Provider market catalog for Inqsi ARB.

The service expands requested market families into concrete provider keys.
Unknown keys are ignored by the provider adapter; supported keys are discovered
per event and retained with provenance. This keeps the arb engine generic.
"""
from __future__ import annotations

from typing import Dict, Iterable, List

MARKET_FAMILY_KEYS: Dict[str, List[str]] = {
    "moneyline": ["h2h", "h2h_3_way", "draw_no_bet"],
    "spreads": ["spreads", "alternate_spreads"],
    "totals": ["totals", "alternate_totals"],
    "team_totals": ["team_totals", "alternate_team_totals"],
    "periods": [
        "h2h_h1", "spreads_h1", "totals_h1",
        "h2h_q1", "spreads_q1", "totals_q1",
        "h2h_p1", "spreads_p1", "totals_p1",
        "h2h_1st_1_innings", "h2h_1st_3_innings", "h2h_1st_5_innings", "h2h_1st_7_innings",
        "spreads_1st_5_innings", "totals_1st_5_innings",
    ],
    "player_props": [
        "player_points", "player_rebounds", "player_assists", "player_threes",
        "player_pass_tds", "player_pass_yds", "player_rush_yds", "player_receptions", "player_reception_yds",
        "batter_hits", "batter_home_runs", "batter_total_bases", "pitcher_strikeouts",
        "player_goals", "player_shots_on_goal", "player_points_power_play",
        "player_aces", "player_double_faults",
    ],
    "team_props": ["team_totals", "team_totals_h1", "team_totals_q1", "team_totals_p1"],
    "game_props": ["both_teams_to_score", "draw_no_bet", "double_chance"],
    "futures": ["outrights"],
}


def expand_market_families(values: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for raw in values:
        key = str(raw).strip().lower()
        candidates = MARKET_FAMILY_KEYS.get(key, [key])
        for market in candidates:
            if market not in seen:
                seen.add(market)
                out.append(market)
    return out
