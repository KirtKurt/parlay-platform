"""Additional reviewed US state house-rule packs.

Only states whose official house-rules page has been read for the registered
sports are listed here. Matching material fields reuse the existing FanDuel
settlement class so AZ baseball can verify against DraftKings the same way NY
does. Unread states stay fail-closed.
"""
from __future__ import annotations

from rules import _register_full_game_triplet, register, Rule

AZ_SOURCE = "https://www.fanduel.com/fanduel-sportsbook-house-rules-az"
AZ_REVIEW = "2026-09-15"


def _fanduel_az_baseball() -> None:
    register(Rule(
        book="fanduel", sport="baseball", market_family="winner", jurisdiction="az",
        reviewed=True, version=AZ_REVIEW, source=AZ_SOURCE,
        settlement_profile="mlb_full_game_2way_action_v1",
        overtime=True, listed_pitcher=False,
        shortened_game_policy="official_after_5_or_4.5_home_leading",
        push_policy="tie_push",
        notes="AZ house rules (effective 2026-08-13): 5/4.5 official-game moneyline, extra innings included, 48h resume window. Same class as NY/IN FanDuel baseball.",
    ))
    for family in ("spreads", "totals"):
        register(Rule(
            book="fanduel", sport="baseball", market_family=family, jurisdiction="az",
            reviewed=True, version=AZ_REVIEW, source=AZ_SOURCE,
            settlement_profile="mlb_full_game_9_or_8.5_v1",
            overtime=True, listed_pitcher=False,
            shortened_game_policy="9_or_8.5_home_leading_unless_unconditionally_determined",
            push_policy="push",
            notes="AZ house rules: 9/8.5 run-line/total unless already determined. Same class as NY/IN FanDuel baseball.",
        ))


_fanduel_az_baseball()
_register_full_game_triplet(
    book="fanduel", sport="americanfootball", jurisdiction="az", source=AZ_SOURCE,
    profile="fd_ny_football_full_game_v1", overtime=True,
    shortened_game_policy="suspended_before_required_time_void_if_not_completed_within_24h;abandoned_or_postponed_60h",
    notes="AZ house rules reviewed 2026-09-15: 24h completion / 60h abandoned windows match NY FanDuel football. Periods and props excluded.",
    version=AZ_REVIEW,
)
_register_full_game_triplet(
    book="fanduel", sport="basketball", jurisdiction="az", source=AZ_SOURCE,
    profile="fd_ny_basketball_full_game_v1", overtime=True,
    shortened_game_policy="nba_or_ncaa_requires_full_regulation_completion_within_24h_unless_market_pre_determined",
    notes="AZ house rules reviewed 2026-09-15: NBA 48 minutes within 24h matches NY FanDuel basketball. Quarters/props excluded.",
    version=AZ_REVIEW,
)
