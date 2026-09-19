"""Reviewed BetMGM settlement profiles.

Only jurisdictions whose official help pages were read are registered. BetMGM
help sites are state-scoped (help.{state}.betmgm.com). Material fields that
differ from DraftKings/FanDuel keep distinct settlement profiles so those
pairs stay fail-closed instead of being invented as equivalent.
"""
from __future__ import annotations

from rules import Rule, _register_full_game_triplet, register

REVIEW = "2026-09-15"
MI_WAGER = "https://help.mi.betmgm.com/en/sports-help/sports-house-rules/sports-wagering-rules"
MI_BASEBALL = "https://help.mi.betmgm.com/en/sports-help/sports-house-rules/baseball-wager-types-and-rules"
NJ_WAGER = "https://help.nj.betmgm.com/en/sports-help/sports-house-rules/sports-wagering-rules"


def _baseball(
    *, jurisdiction: str, source: str, shortened_winner: str,
    shortened_spread: str, winner_profile: str, spread_profile: str,
    notes: str,
) -> None:
    register(Rule(
        book="betmgm", sport="baseball", market_family="winner", jurisdiction=jurisdiction,
        reviewed=True, version=REVIEW, source=source,
        settlement_profile=winner_profile,
        overtime=True, listed_pitcher=False,
        shortened_game_policy=shortened_winner,
        push_policy="tie_push",
        notes=notes,
    ))
    for family in ("spreads", "totals"):
        register(Rule(
            book="betmgm", sport="baseball", market_family=family, jurisdiction=jurisdiction,
            reviewed=True, version=REVIEW, source=source,
            settlement_profile=spread_profile,
            overtime=True, listed_pitcher=False,
            shortened_game_policy=shortened_spread,
            push_policy="push",
            notes=notes,
        ))


_baseball(
    jurisdiction="mi", source=MI_BASEBALL,
    shortened_winner="official_after_5_or_4.5_home_leading;suspension_over_36h_void_unless_determined",
    shortened_spread="9_or_8.5_home_leading_unless_unconditionally_determined;suspension_over_36h_void_unless_determined",
    winner_profile="betmgm_mlb_full_game_2way_action_36h_v1",
    spread_profile="betmgm_mlb_full_game_9_or_8.5_36h_v1",
    notes="MI BetMGM baseball pages reviewed 2026-09-15: 5/4.5 moneyline, 9/8.5 run-line/total, extra innings included, 36h resume. Listed-pitcher variants excluded.",
)
_baseball(
    jurisdiction="nj", source=NJ_WAGER,
    shortened_winner="official_after_5_or_4.5_home_leading",
    shortened_spread="9_or_8.5_home_leading_unless_unconditionally_determined",
    winner_profile="betmgm_nj_mlb_full_game_2way_resume_unverified_v1",
    spread_profile="betmgm_nj_mlb_full_game_9_or_8.5_resume_unverified_v1",
    notes="NJ BetMGM sports wagering rules reviewed 2026-09-15: 5/4.5 official-game moneyline. 36h resume was not on the NJ wagering-rules page so it is not claimed.",
)

for _jurisdiction, _source in (("mi", MI_WAGER), ("nj", NJ_WAGER)):
    _register_full_game_triplet(
        book="betmgm", sport="americanfootball", jurisdiction=_jurisdiction, source=_source,
        profile="betmgm_football_55_minute_v1", overtime=True,
        shortened_game_policy="official_after_55_minutes;abandoned_before_55_void_unless_unconditionally_determined",
        notes=f"{_jurisdiction.upper()} BetMGM: pro/college football official after 55 minutes. Distinct from DraftKings 48h and FanDuel 24h/60h windows.",
        version=REVIEW,
    )
    _register_full_game_triplet(
        book="betmgm", sport="basketball", jurisdiction=_jurisdiction, source=_source,
        profile="betmgm_basketball_43_or_35_v1", overtime=True,
        shortened_game_policy="nba_43_minutes;ncaa_wnba_summer_european_35_minutes",
        notes=f"{_jurisdiction.upper()} BetMGM: NBA 43 minutes / NCAA-WNBA-European 35 minutes. Distinct from FanDuel 48-minute/24h completion.",
        version=REVIEW,
    )
    _register_full_game_triplet(
        book="betmgm", sport="icehockey", jurisdiction=_jurisdiction, source=_source,
        profile="betmgm_hockey_55_or_60_v1", overtime=True,
        shortened_game_policy="us_pro_55_minutes;non_us_60_minutes",
        notes=f"{_jurisdiction.upper()} BetMGM: US pro hockey 55 minutes, non-US 60 minutes.",
        version=REVIEW,
    )
    _register_full_game_triplet(
        book="betmgm", sport="soccer", jurisdiction=_jurisdiction, source=_source,
        profile="betmgm_soccer_regulation_v1", overtime=False,
        shortened_game_policy="regulation_90_plus_injury_for_goal_line_and_totals",
        notes=f"{_jurisdiction.upper()} BetMGM: soccer 90 minutes plus injury time for goal line and totals. Extra time excluded unless stated.",
        version=REVIEW,
    )
    for _family in ("winner", "spreads", "totals"):
        register(Rule(
            book="betmgm", sport="tennis", market_family=_family, jurisdiction=_jurisdiction,
            reviewed=True, version=REVIEW, source=_source,
            settlement_profile="betmgm_tennis_full_match_v1",
            participation_required=True,
            retirement_policy="after_start_retiring_selection_cancelled;progressing_selection_wins_match_betting",
            shortened_game_policy="interrupted_match_cancelled_if_not_naturally_concluded_unless_unconditionally_determined",
            push_policy="market_specific",
            notes=f"{_jurisdiction.upper()} BetMGM tennis: retiring player cancelled, progressing player wins match betting. Kept distinct from other books.",
        ))
