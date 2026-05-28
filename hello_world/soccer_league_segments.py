from __future__ import annotations

from typing import Any, Dict

LEAGUE_PROFILE_VERSION = "soccer_league_segments_v1"

SOCCER_LEAGUE_SEGMENTS: Dict[str, Dict[str, Any]] = {
    "soccer_brazil_campeonato": {"league_segment": "brazil_serie_a", "league_name": "Brazil Série A"},
    "soccer_brazil_serie_b": {"league_segment": "brazil_serie_b", "league_name": "Brazil Série B"},
    "soccer_chile_campeonato": {"league_segment": "chile_primera", "league_name": "Chile Primera"},
    "soccer_china_superleague": {"league_segment": "china_super_league", "league_name": "China Super League"},
    "soccer_conmebol_copa_libertadores": {"league_segment": "copa_libertadores", "league_name": "Copa Libertadores"},
    "soccer_conmebol_copa_sudamericana": {"league_segment": "copa_sudamericana", "league_name": "Copa Sudamericana"},
    "soccer_finland_veikkausliiga": {"league_segment": "finland_veikkausliiga", "league_name": "Finland Veikkausliiga"},
    "soccer_japan_j_league": {"league_segment": "japan_j_league", "league_name": "Japan J League"},
    "soccer_league_of_ireland": {"league_segment": "league_of_ireland", "league_name": "League of Ireland"},
    "soccer_norway_eliteserien": {"league_segment": "norway_eliteserien", "league_name": "Norway Eliteserien"},
    "soccer_spain_segunda_division": {"league_segment": "spain_segunda", "league_name": "Spain Segunda"},
    "soccer_sweden_allsvenskan": {"league_segment": "sweden_allsvenskan", "league_name": "Sweden Allsvenskan"},
    "soccer_sweden_superettan": {"league_segment": "sweden_superettan", "league_name": "Sweden Superettan"},
}

DEFAULT_LEAGUE_PROFILE: Dict[str, Any] = {
    "league_segment": "unknown_soccer_league",
    "league_name": "Unknown Soccer League",
    "draw_sensitivity": "unknown",
    "favorite_reliability": "unknown",
    "market_compression_tendency": "unknown",
    "goal_environment": "unknown",
    "book_coverage_quality": "unknown",
    "volatility_level": "unknown",
    "spread_confirmation_weight": "medium",
    "total_confirmation_weight": "medium",
    "parlay_eligibility_weight": "low_until_profiled",
}

DEFAULT_PROFILE_TRAITS: Dict[str, Any] = {
    "draw_sensitivity": "medium",
    "favorite_reliability": "medium",
    "market_compression_tendency": "medium",
    "goal_environment": "medium",
    "book_coverage_quality": "medium",
    "volatility_level": "medium",
    "spread_confirmation_weight": "medium",
    "total_confirmation_weight": "medium",
    "parlay_eligibility_weight": "medium",
}


def get_soccer_league_profile(sport_key: str) -> Dict[str, Any]:
    profile = dict(DEFAULT_LEAGUE_PROFILE)
    if sport_key in SOCCER_LEAGUE_SEGMENTS:
        profile.update(DEFAULT_PROFILE_TRAITS)
        profile.update(SOCCER_LEAGUE_SEGMENTS[sport_key])
    profile["sport_key"] = sport_key
    profile["league_profile_version"] = LEAGUE_PROFILE_VERSION
    profile["model_isolation"] = "soccer_only_three_way_no_cross_sport_bleed"
    return profile
