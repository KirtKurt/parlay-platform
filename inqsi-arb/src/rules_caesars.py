"""Reviewed Caesars (Odds API key williamhill_us) settlement profiles.

Caesars publishes a national house-rules page (effective 2026-05-26). Profiles
stay Caesars-specific. Baseball moneyline requiring 9/8.5 is materially
different from DraftKings/FanDuel 5/4.5 and must never cross-qualify.
"""
from __future__ import annotations

from rules import Rule, _register_full_game_triplet, register

REVIEW = "2026-09-15"
SOURCE = "https://www.caesars.com/sportsbook-and-casino/support/house-rules"


register(Rule(
    book="williamhill_us", sport="baseball", market_family="winner", jurisdiction="*",
    reviewed=True, version=REVIEW, source=SOURCE,
    settlement_profile="caesars_mlb_full_game_9_or_8.5_v1",
    overtime=True, listed_pitcher=False,
    shortened_game_policy="9_or_8.5_home_ahead_unless_unconditionally_determined;doubleheader_7_or_6.5;postponed_same_day_void_except_mlb_playoffs",
    push_policy="tie_push",
    notes="Caesars national rules: all baseball wagers including moneyline need 9/8.5 unless already determined. Action regardless of pitcher except listed-pitcher vs listed-pitcher. Distinct from DK/FD 5/4.5 moneyline.",
))
for _family in ("spreads", "totals"):
    register(Rule(
        book="williamhill_us", sport="baseball", market_family=_family, jurisdiction="*",
        reviewed=True, version=REVIEW, source=SOURCE,
        settlement_profile="caesars_mlb_full_game_9_or_8.5_v1",
        overtime=True, listed_pitcher=False,
        shortened_game_policy="9_or_8.5_home_ahead_unless_unconditionally_determined;doubleheader_7_or_6.5;postponed_same_day_void_except_mlb_playoffs",
        push_policy="push",
        notes="Caesars national baseball run-line/total: 9/8.5 unless already determined. Extra innings count.",
    ))

_register_full_game_triplet(
    book="williamhill_us", sport="americanfootball", jurisdiction="*", source=SOURCE,
    profile="caesars_football_50_minute_v1", overtime=True,
    shortened_game_policy="official_after_50_minutes;suspended_or_postponed_must_be_played_within_7_days_except_championship",
    notes="Caesars national football: 50 minutes of play for action, overtime included. Distinct from BetMGM 55 minutes and FanDuel 24h/60h.",
    version=REVIEW,
)
_register_full_game_triplet(
    book="williamhill_us", sport="basketball", jurisdiction="*", source=SOURCE,
    profile="caesars_basketball_43_or_35_v1", overtime=True,
    shortened_game_policy="nba_43_minutes;college_international_35_minutes;must_play_scheduled_date",
    notes="Caesars national basketball: NBA 43 minutes / other 35 minutes, overtime included unless stated. Two-way ties void unless a tie price is quoted.",
    version=REVIEW,
)
_register_full_game_triplet(
    book="williamhill_us", sport="icehockey", jurisdiction="*", source=SOURCE,
    profile="caesars_hockey_55_minute_v1", overtime=True,
    shortened_game_policy="official_after_55_minutes;north_american_includes_ot_and_shootout;non_na_regulation_unless_stated",
    notes="Caesars national hockey: 55 minutes of play. North American OT/shootout included; IIHF/international uses non-NA rules.",
    version=REVIEW,
)
_register_full_game_triplet(
    book="williamhill_us", sport="soccer", jurisdiction="*", source=SOURCE,
    profile="caesars_soccer_regulation_v1", overtime=False,
    shortened_game_policy="regulation_90_plus_stoppage;postponed_void_unless_rescheduled_within_48h_or_listed_competition_exception",
    notes="Caesars national soccer: 90 minutes plus stoppage; extra time and penalties excluded unless stated.",
    version=REVIEW,
)
for _family in ("winner", "spreads", "totals"):
    register(Rule(
        book="williamhill_us", sport="tennis", market_family=_family, jurisdiction="*",
        reviewed=True, version=REVIEW, source=SOURCE,
        settlement_profile="caesars_tennis_full_match_v1",
        participation_required=True,
        retirement_policy="before_first_set_complete_void;after_first_set_retiring_selection_loses_progressor_wins",
        shortened_game_policy="suspended_stands_if_completed_before_end_of_competition_else_void_unless_determined",
        push_policy="market_specific",
        notes="Caesars national tennis: retirement before first set voids; after first set the retiring player loses. Distinct from FanDuel/BetMGM after-start progressor rules.",
    ))
