"""Static safety boundaries and dynamic-discovery fallbacks for soccer_auto."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class Competition:
    key: str
    title: str
    scores_supported: bool = True
    outright: bool = False


# The published catalog is a fallback and an audit boundary, not the live source
# of truth.  The collector refreshes /v4/sports?all=true on every catalog cycle
# and automatically admits new keys whose group is Soccer or key starts soccer_.
PUBLISHED_COMPETITIONS: Final[tuple[Competition, ...]] = (
    Competition("soccer_africa_cup_of_nations", "Africa Cup of Nations", False),
    Competition("soccer_argentina_primera_division", "Argentina Primera Division"),
    Competition("soccer_australia_aleague", "Australia A-League"),
    Competition("soccer_austria_bundesliga", "Austria Bundesliga"),
    Competition("soccer_belgium_first_div", "Belgium First Division"),
    Competition("soccer_brazil_campeonato", "Brazil Serie A"),
    Competition("soccer_brazil_serie_b", "Brazil Serie B"),
    Competition("soccer_chile_campeonato", "Chile Primera Division"),
    Competition("soccer_china_superleague", "China Super League"),
    Competition("soccer_denmark_superliga", "Denmark Superliga"),
    Competition("soccer_efl_champ", "EFL Championship"),
    Competition("soccer_england_efl_cup", "EFL Cup"),
    Competition("soccer_england_league1", "England League One"),
    Competition("soccer_england_league2", "England League Two"),
    Competition("soccer_epl", "English Premier League"),
    Competition("soccer_fa_cup", "FA Cup"),
    Competition("soccer_fifa_world_cup", "FIFA World Cup"),
    Competition("soccer_fifa_world_cup_qualifiers_europe", "World Cup Qualifiers Europe"),
    Competition("soccer_fifa_world_cup_qualifiers_south_america", "World Cup Qualifiers South America"),
    Competition("soccer_fifa_world_cup_womens", "FIFA Women's World Cup"),
    Competition("soccer_fifa_world_cup_winner", "FIFA World Cup Winner", False, True),
    Competition("soccer_fifa_club_world_cup", "FIFA Club World Cup"),
    Competition("soccer_finland_veikkausliiga", "Finland Veikkausliiga"),
    Competition("soccer_france_coupe_de_france", "Coupe de France"),
    Competition("soccer_france_ligue_one", "France Ligue 1"),
    Competition("soccer_france_ligue_two", "France Ligue 2"),
    Competition("soccer_germany_bundesliga", "Germany Bundesliga"),
    Competition("soccer_germany_bundesliga2", "Germany Bundesliga 2"),
    Competition("soccer_germany_bundesliga_women", "Frauen-Bundesliga"),
    Competition("soccer_germany_dfb_pokal", "DFB-Pokal"),
    Competition("soccer_germany_liga3", "Germany 3. Liga"),
    Competition("soccer_greece_super_league", "Greece Super League"),
    Competition("soccer_italy_coppa_italia", "Coppa Italia"),
    Competition("soccer_italy_serie_a", "Italy Serie A"),
    Competition("soccer_italy_serie_b", "Italy Serie B"),
    Competition("soccer_japan_j_league", "Japan J League"),
    Competition("soccer_korea_kleague1", "Korea K League 1"),
    Competition("soccer_league_of_ireland", "League of Ireland"),
    Competition("soccer_mexico_ligamx", "Liga MX"),
    Competition("soccer_netherlands_eredivisie", "Netherlands Eredivisie"),
    Competition("soccer_norway_eliteserien", "Norway Eliteserien"),
    Competition("soccer_poland_ekstraklasa", "Poland Ekstraklasa"),
    Competition("soccer_portugal_primeira_liga", "Portugal Primeira Liga"),
    Competition("soccer_russia_premier_league", "Russia Premier League"),
    Competition("soccer_spain_copa_del_rey", "Copa del Rey"),
    Competition("soccer_spain_la_liga", "Spain La Liga"),
    Competition("soccer_spain_segunda_division", "Spain La Liga 2"),
    Competition("soccer_saudi_arabia_pro_league", "Saudi Pro League"),
    Competition("soccer_spl", "Scottish Premiership"),
    Competition("soccer_sweden_allsvenskan", "Sweden Allsvenskan"),
    Competition("soccer_sweden_superettan", "Sweden Superettan"),
    Competition("soccer_switzerland_superleague", "Swiss Super League"),
    Competition("soccer_turkey_super_league", "Turkey Super League"),
    Competition("soccer_uefa_europa_conference_league", "UEFA Europa Conference League"),
    Competition("soccer_uefa_champs_league", "UEFA Champions League"),
    Competition("soccer_uefa_champs_league_qualification", "UEFA Champions League Qualification"),
    Competition("soccer_uefa_champs_league_women", "UEFA Women's Champions League"),
    Competition("soccer_uefa_europa_league", "UEFA Europa League"),
    Competition("soccer_uefa_european_championship", "UEFA European Championship"),
    Competition("soccer_uefa_euro_qualification", "UEFA Euro Qualification"),
    Competition("soccer_uefa_nations_league", "UEFA Nations League"),
    Competition("soccer_concacaf_gold_cup", "CONCACAF Gold Cup", False),
    Competition("soccer_concacaf_leagues_cup", "CONCACAF Leagues Cup"),
    Competition("soccer_conmebol_copa_america", "Copa America"),
    Competition("soccer_conmebol_copa_libertadores", "Copa Libertadores"),
    Competition("soccer_conmebol_copa_sudamericana", "Copa Sudamericana"),
    Competition("soccer_usa_mls", "MLS"),
)

PUBLISHED_KEYS: Final[tuple[str, ...]] = tuple(row.key for row in PUBLISHED_COMPETITIONS)
PUBLISHED_SCORE_SUPPORT: Final[dict[str, bool]] = {
    row.key: row.scores_supported for row in PUBLISHED_COMPETITIONS
}

# Passing all regions and omitting the bookmakers parameter is the provider's
# supported way to request every sportsbook in those regions.  Overlapping books
# are de-duplicated by key in canonicalization.
ALL_BOOKMAKER_REGIONS: Final[tuple[str, ...]] = (
    "us", "us2", "us_dfs", "us_ex", "uk", "eu", "fr", "se", "au"
)

FEATURED_GAME_MARKETS: Final[tuple[str, ...]] = ("h2h", "spreads", "totals")
FEATURED_OUTRIGHT_MARKETS: Final[tuple[str, ...]] = ("outrights",)

# Seeds prevent a newly opened market from being missed between event-market
# discovery calls.  Runtime discovery is authoritative and may add any key.
SOCCER_MARKET_SEEDS: Final[tuple[str, ...]] = (
    "h2h", "h2h_lay", "h2h_3_way", "spreads", "totals",
    "outrights", "outrights_lay", "alternate_spreads", "alternate_totals",
    "team_totals", "alternate_team_totals", "btts", "draw_no_bet",
    "h2h_h1", "h2h_h2", "h2h_3_way_h1", "h2h_3_way_h2",
    "spreads_h1", "spreads_h2", "alternate_spreads_h1", "alternate_spreads_h2",
    "totals_h1", "totals_h2", "alternate_totals_h1", "alternate_totals_h2",
    "team_totals_h1", "team_totals_h2", "alternate_team_totals_h1",
    "alternate_team_totals_h2", "btts_h1", "correct_score", "correct_score_h1",
    "double_chance", "double_chance_h1", "halftime_fulltime", "to_qualify",
    "corners_1x2", "alternate_spreads_corners", "alternate_totals_corners",
    "alternate_team_totals_corners", "alternate_spreads_cards", "alternate_totals_cards",
    "player_goal_scorer_anytime", "player_first_goal_scorer", "player_last_goal_scorer",
    "player_to_receive_card", "player_to_receive_red_card", "player_shots_on_target",
    "player_shots", "player_assists",
)

PLAYER_PROP_COMPETITIONS: Final[frozenset[str]] = frozenset({
    "soccer_epl",
    "soccer_france_ligue_one",
    "soccer_germany_bundesliga",
    "soccer_italy_serie_a",
    "soccer_spain_la_liga",
    "soccer_usa_mls",
})

PLAYER_MARKET_PREFIXES: Final[tuple[str, ...]] = ("player_",)

# Information-value cadence.  The one-minute scheduler asks what is due; it does
# not blindly call the provider every minute for every future event.
CADENCE_SECONDS_BY_HOURS_TO_START: Final[tuple[tuple[float, int], ...]] = (
    (0.0, 60),          # live/recently due
    (6.0, 300),         # final six hours
    (float("inf"), 900),  # every open match-day game, at least every 15 minutes
)

HISTORICAL_FEATURED_START: Final[str] = "2020-06-06T00:00:00Z"
HISTORICAL_ADDITIONAL_START: Final[str] = "2023-05-03T05:30:00Z"
