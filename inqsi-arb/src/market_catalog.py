"""Provider-documented market catalog for Inqsi ARB.

This module contains the current market-key universe used by The Odds API and
selects sport-appropriate candidates automatically. Runtime probing determines
which keys are actually available for a specific event/book set.
"""
from __future__ import annotations

from typing import Dict, Iterable, List

FEATURED = ["h2h", "spreads", "totals", "outrights", "h2h_lay", "outrights_lay"]
ADDITIONAL = [
    "alternate_spreads", "alternate_totals", "btts", "draw_no_bet", "h2h_3_way",
    "team_totals", "alternate_team_totals",
]
PERIODS = [
    "h2h_q1","h2h_q2","h2h_q3","h2h_q4","h2h_h1","h2h_h2",
    "h2h_p1","h2h_p2","h2h_p3",
    "h2h_3_way_q1","h2h_3_way_q2","h2h_3_way_q3","h2h_3_way_q4","h2h_3_way_h1","h2h_3_way_h2",
    "h2h_3_way_p1","h2h_3_way_p2","h2h_3_way_p3",
    "h2h_1st_1_innings","h2h_1st_3_innings","h2h_1st_5_innings","h2h_1st_7_innings",
    "h2h_3_way_1st_1_innings","h2h_3_way_1st_3_innings","h2h_3_way_1st_5_innings","h2h_3_way_1st_7_innings",
    "h2h_s1","h2h_s2",
    "spreads_q1","spreads_q2","spreads_q3","spreads_q4","spreads_h1","spreads_h2",
    "spreads_p1","spreads_p2","spreads_p3",
    "spreads_1st_1_innings","spreads_1st_3_innings","spreads_1st_5_innings","spreads_1st_7_innings","spreads_s1",
    "alternate_spreads_1st_1_innings","alternate_spreads_1st_3_innings","alternate_spreads_1st_5_innings","alternate_spreads_1st_7_innings",
    "alternate_spreads_q1","alternate_spreads_q2","alternate_spreads_q3","alternate_spreads_q4","alternate_spreads_h1","alternate_spreads_h2",
    "alternate_spreads_p1","alternate_spreads_p2","alternate_spreads_p3",
    "totals_q1","totals_q2","totals_q3","totals_q4","totals_h1","totals_h2","totals_p1","totals_p2","totals_p3",
    "totals_1st_1_innings","totals_1st_3_innings","totals_1st_5_innings","totals_1st_7_innings","totals_s1",
    "alternate_totals_1st_1_innings","alternate_totals_1st_3_innings","alternate_totals_1st_5_innings","alternate_totals_1st_7_innings","alternate_totals_s1",
    "alternate_totals_q1","alternate_totals_q2","alternate_totals_q3","alternate_totals_q4","alternate_totals_h1","alternate_totals_h2",
    "alternate_totals_p1","alternate_totals_p2","alternate_totals_p3",
    "team_totals_h1","team_totals_h2","team_totals_q1","team_totals_q2","team_totals_q3","team_totals_q4","team_totals_p1","team_totals_p2","team_totals_p3",
    "alternate_team_totals_h1","alternate_team_totals_h2","alternate_team_totals_q1","alternate_team_totals_q2","alternate_team_totals_q3","alternate_team_totals_q4",
    "alternate_team_totals_p1","alternate_team_totals_p2","alternate_team_totals_p3",
]

FOOTBALL_PLAYER = [
    "player_assists","player_defensive_interceptions","player_field_goals","player_kicking_points","player_pass_attempts",
    "player_pass_completions","player_pass_interceptions","player_pass_longest_completion","player_pass_rush_yds",
    "player_pass_rush_reception_tds","player_pass_rush_reception_yds","player_pass_tds","player_pass_yds","player_pass_yds_q1",
    "player_pats","player_receptions","player_reception_longest","player_reception_tds","player_reception_yds","player_rush_attempts",
    "player_rush_longest","player_rush_reception_tds","player_rush_reception_yds","player_rush_tds","player_rush_yds","player_sacks",
    "player_solo_tackles","player_tackles_assists","player_tds_over","player_1st_td","player_anytime_td","player_last_td",
]
FOOTBALL_ALT = [x + "_alternate" for x in [
    "player_assists","player_field_goals","player_kicking_points","player_pass_attempts","player_pass_completions","player_pass_interceptions",
    "player_pass_longest_completion","player_pass_rush_yds","player_pass_rush_reception_tds","player_pass_rush_reception_yds","player_pass_tds",
    "player_pass_yds","player_pats","player_receptions","player_reception_longest","player_reception_tds","player_reception_yds","player_rush_attempts",
    "player_rush_longest","player_rush_reception_tds","player_rush_reception_yds","player_rush_tds","player_rush_yds","player_sacks",
    "player_solo_tackles","player_tackles_assists",
]]
BASKETBALL_PLAYER = [
    "player_points","player_points_q1","player_rebounds","player_rebounds_q1","player_assists","player_assists_q1","player_threes",
    "player_blocks","player_steals","player_blocks_steals","player_turnovers","player_points_rebounds_assists","player_points_rebounds",
    "player_points_assists","player_rebounds_assists","player_field_goals","player_frees_made","player_frees_attempts","player_first_basket",
    "player_first_team_basket","player_double_double","player_triple_double","player_method_of_first_basket","player_fantasy_points",
]
BASKETBALL_ALT = [x + "_alternate" for x in [
    "player_points","player_rebounds","player_assists","player_blocks","player_steals","player_turnovers","player_threes",
    "player_points_assists","player_points_rebounds","player_rebounds_assists","player_points_rebounds_assists","player_fantasy_points",
]]
BASEBALL_PLAYER = [
    "batter_home_runs","batter_first_home_run","batter_hits","batter_total_bases","batter_rbis","batter_runs_scored","batter_hits_runs_rbis",
    "batter_singles","batter_doubles","batter_triples","batter_walks","batter_strikeouts","batter_stolen_bases","batter_fantasy_score",
    "pitcher_strikeouts","pitcher_record_a_win","pitcher_hits_allowed","pitcher_walks","pitcher_earned_runs","pitcher_outs",
]
BASEBALL_ALT = [
    "batter_total_bases_alternate","batter_home_runs_alternate","batter_hits_alternate","batter_rbis_alternate","batter_walks_alternate",
    "batter_strikeouts_alternate","batter_runs_scored_alternate","batter_hits_runs_rbis_alternate","batter_singles_alternate",
    "batter_doubles_alternate","batter_triples_alternate","batter_fantasy_score_alternate","pitcher_hits_allowed_alternate",
    "pitcher_walks_alternate","pitcher_earned_runs_alternate","pitcher_strikeouts_alternate","pitcher_outs_alternate",
]
HOCKEY_PLAYER = [
    "player_points","player_power_play_points","player_assists","player_blocked_shots","player_shots_on_goal","player_goals","player_total_saves",
    "player_goal_scorer_first","player_goal_scorer_last","player_goal_scorer_anytime",
]
HOCKEY_ALT = [x + "_alternate" for x in [
    "player_points","player_assists","player_power_play_points","player_goals","player_shots_on_goal","player_blocked_shots","player_total_saves",
]]
AFL_PLAYER = [
    "player_disposals","player_disposals_over","player_goal_scorer_first","player_goal_scorer_last","player_goal_scorer_anytime",
    "player_goals_scored_over","player_marks_over","player_marks_most","player_tackles_over","player_tackles_most","player_afl_fantasy_points",
    "player_afl_fantasy_points_over","player_afl_fantasy_points_most","player_clearances_over","player_kicks_over","player_handballs_over",
]
RUGBY_LEAGUE_PLAYER = ["player_try_scorer_first","player_try_scorer_last","player_try_scorer_anytime","player_try_scorer_over"]
SOCCER_PLAYER = [
    "player_goal_scorer_anytime","player_first_goal_scorer","player_last_goal_scorer","player_to_receive_card","player_to_receive_red_card",
    "player_shots_on_target","player_shots","player_assists",
]
SOCCER_GAME = [
    "alternate_spreads_corners","alternate_totals_corners","alternate_spreads_cards","alternate_team_totals_corners","alternate_totals_cards",
    "btts","btts_h1","correct_score","correct_score_h1","corners_1x2","double_chance","double_chance_h1","halftime_fulltime","to_qualify",
]

MARKET_FAMILY_KEYS: Dict[str, List[str]] = {
    "moneyline": ["h2h","h2h_3_way","draw_no_bet"],
    "spreads": ["spreads","alternate_spreads"],
    "totals": ["totals","alternate_totals"],
    "team_totals": ["team_totals","alternate_team_totals"],
    "periods": PERIODS,
    "player_props": sorted(set(FOOTBALL_PLAYER + FOOTBALL_ALT + BASKETBALL_PLAYER + BASKETBALL_ALT + BASEBALL_PLAYER + BASEBALL_ALT + HOCKEY_PLAYER + HOCKEY_ALT + AFL_PLAYER + RUGBY_LEAGUE_PLAYER + SOCCER_PLAYER)),
    "team_props": [x for x in PERIODS if x.startswith("team_totals") or x.startswith("alternate_team_totals")],
    "game_props": sorted(set(["btts","draw_no_bet","h2h_3_way"] + SOCCER_GAME)),
    "futures": ["outrights","outrights_lay"],
    "exchange": ["h2h_lay","outrights_lay"],
}


def _dedupe(values: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in values:
        key = str(value).strip()
        if key and key not in seen:
            seen.add(key); out.append(key)
    return out


def candidate_markets_for_sport(sport_key: str) -> List[str]:
    key = str(sport_key or "").lower()
    base = FEATURED + ADDITIONAL + PERIODS
    if "americanfootball" in key:
        base += FOOTBALL_PLAYER + FOOTBALL_ALT
    elif "basketball" in key:
        base += BASKETBALL_PLAYER + BASKETBALL_ALT
    elif "baseball" in key:
        base += BASEBALL_PLAYER + BASEBALL_ALT
    elif "icehockey" in key or "hockey" in key:
        base += HOCKEY_PLAYER + HOCKEY_ALT
    elif "aussierules" in key or "afl" in key:
        base += AFL_PLAYER
    elif "rugbyleague" in key:
        base += RUGBY_LEAGUE_PLAYER
    elif "soccer" in key:
        base += SOCCER_PLAYER + SOCCER_GAME
    # For other sports use provider-supported featured/additional/period keys; the
    # runtime probe will retain only keys accepted for that event.
    return _dedupe(base)


def expand_market_families(values: Iterable[str]) -> List[str]:
    out: List[str] = []
    for raw in values:
        key = str(raw).strip().lower()
        out.extend(MARKET_FAMILY_KEYS.get(key, [key]))
    return _dedupe(out)
