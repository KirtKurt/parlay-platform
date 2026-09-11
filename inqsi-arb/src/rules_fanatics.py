"""Reviewed Fanatics Sportsbook New York settlement profiles.

This module is imported for registration side effects by validation.py. Profiles
are intentionally kept Fanatics-specific unless settlement equivalence with
another sportsbook has been fully demonstrated. This expands rule coverage
without widening the verified-arbitrage trust boundary.
"""
from __future__ import annotations

from rules import REVIEW_DATE, Rule, register

_SOURCE = "https://sportsbook.fanatics.com/legal/ny/house-rules"


def _triplet(*, sport: str, profile: str, overtime: bool, shortened: str, notes: str) -> None:
    for family in ("winner", "spreads", "totals"):
        register(Rule(
            book="fanatics",
            sport=sport,
            market_family=family,
            jurisdiction="ny",
            reviewed=True,
            version=REVIEW_DATE,
            source=_SOURCE,
            settlement_profile=profile,
            overtime=overtime,
            participation_required=False,
            shortened_game_policy=shortened,
            push_policy="push",
            notes=notes,
        ))


# Full-game action baseball only. Listed-pitcher variants, 3-way markets,
# periods, player props, and futures remain outside these rows.
register(Rule(
    book="fanatics",
    sport="baseball",
    market_family="winner",
    jurisdiction="ny",
    reviewed=True,
    version=REVIEW_DATE,
    source=_SOURCE,
    settlement_profile="fanatics_ny_mlb_full_game_2way_action_v1",
    overtime=True,
    listed_pitcher=False,
    participation_required=False,
    shortened_game_policy="called_early_requires_4.5_home_leading_or_5_away_leading;tie_or_away_lead_during_bottom_5_void;suspension_over_48h_void_unless_determined",
    push_policy="tie_push",
    notes="Fanatics NY default full-game 2-way baseball moneyline, inclusive of extra innings; listed-pitcher and 3-way variants excluded.",
))
for _family in ("spreads", "totals"):
    register(Rule(
        book="fanatics",
        sport="baseball",
        market_family=_family,
        jurisdiction="ny",
        reviewed=True,
        version=REVIEW_DATE,
        source=_SOURCE,
        settlement_profile="fanatics_ny_mlb_full_game_9_or_8.5_v1",
        overtime=True,
        listed_pitcher=False,
        participation_required=False,
        shortened_game_policy="9_or_8.5_home_ahead_unless_market_already_determined;scheduled_7_inning_games_7_or_6.5;suspension_over_48h_void_unless_determined",
        push_policy="push",
        notes="Fanatics NY default full-game MLB run-line/total family; periods, listed-pitcher variants and props excluded.",
    ))

# Fanatics explicitly distinguishes two-way and three-way inning/grouped
# moneylines. Keep those identities separate because a tied period voids the
# two-way market but settles the Tie selection as the winner in a three-way
# market. The specified full innings must be completed unless the home team is
# ahead with one half-inning remaining. Spreads/totals/team totals remain
# unreviewed and therefore fail closed.
register(Rule(
    book="fanatics",
    sport="baseball",
    market_family="period_winner_2way",
    jurisdiction="ny",
    reviewed=True,
    version=REVIEW_DATE,
    source=_SOURCE,
    settlement_profile="fanatics_ny_baseball_period_2way_winner_v1",
    overtime=False,
    listed_pitcher=False,
    participation_required=False,
    shortened_game_policy="specified_full_innings_required_unless_home_team_ahead_with_half_inning_remaining",
    push_policy="tied_period_void",
    notes="Fanatics NY inning/grouped two-way moneyline only, including supported 3/5/7-inning groups; tied period voids. Three-way, spreads, totals and team totals use separate identities.",
))
register(Rule(
    book="fanatics",
    sport="baseball",
    market_family="period_winner_3way",
    jurisdiction="ny",
    reviewed=True,
    version=REVIEW_DATE,
    source=_SOURCE,
    settlement_profile="fanatics_ny_baseball_period_3way_winner_v1",
    overtime=False,
    listed_pitcher=False,
    participation_required=False,
    shortened_game_policy="specified_full_innings_required_unless_home_team_ahead_with_half_inning_remaining",
    push_policy="tie_selection_wins",
    notes="Fanatics NY inning/grouped three-way moneyline only, including supported 3/5/7-inning groups; Tie is a distinct winning selection when scores are level.",
))

_triplet(
    sport="americanfootball",
    profile="fanatics_ny_football_full_game_v1",
    overtime=True,
    shortened="non_playoff_requires_at_least_10_minutes_of_q4_or_completion_within_48h_unless_determined;playoffs_remain_open_until_governing_body_completion",
    notes="Fanatics NY full-game 2-way football winner/spread/total only; period and player markets excluded.",
)

_triplet(
    sport="basketball",
    profile="fanatics_ny_basketball_full_game_v1",
    overtime=True,
    shortened="non_playoff_called_early_action_with_2_minutes_or_less_remaining;otherwise_completion_within_48h_unless_determined;playoffs_remain_open_until_completion",
    notes="Fanatics NY full-game basketball winner/spread/total only; quarter/half, Elam-specific and player markets excluded.",
)

_triplet(
    sport="icehockey",
    profile="fanatics_ny_hockey_full_game_v1",
    overtime=True,
    shortened="non_playoff_called_early_action_with_2_minutes_or_less_remaining;otherwise_completion_within_48h_unless_determined;playoffs_remain_open_until_completion",
    notes="Fanatics NY full-game hockey winner/spread/total including overtime/shootout unless stated otherwise; regulation-only, period and player markets excluded.",
)

_triplet(
    sport="soccer",
    profile="fanatics_ny_soccer_regulation_v1",
    overtime=False,
    shortened="regulation_90_plus_stoppage;postponed_under_48h_stands;format_changes_or_abandonment_follow_sport_specific_void_rules_unless_determined",
    notes="Fanatics NY standard regulation-time soccer winner/spread/total only; extra-time, to-qualify, period and player markets excluded.",
)

# Tennis needs its own retirement semantics. Fanatics pays the participant who
# officially progresses on the moneyline, voids the retiring side, and voids
# undetermined handicap/total markets. Keep it isolated from other books.
for _family in ("winner", "spreads", "totals"):
    register(Rule(
        book="fanatics",
        sport="tennis",
        market_family=_family,
        jurisdiction="ny",
        reviewed=True,
        version=REVIEW_DATE,
        source=_SOURCE,
        settlement_profile="fanatics_ny_tennis_full_match_v1",
        participation_required=True,
        retirement_policy="after_start_official_progressor_wins_moneyline;retiring_selection_void;other_undetermined_markets_void;determined_markets_stand",
        shortened_game_policy="delayed_or_suspended_match_stands_unless_tournament_governing_body_cancels;changed_statutory_sets_void_unless_determined",
        push_policy="market_specific",
        notes="Fanatics NY full-match tennis family; retirement and disqualification treatment is Fanatics-specific and is not cross-qualified with other books.",
    ))
