"""Additional reviewed US state house-rule packs.

Only states whose official house-rules page has been read for the registered
sports are listed here. Matching material fields reuse the existing FanDuel
settlement class so those states can verify against DraftKings the same way NY
does. Unread states stay fail-closed.
"""
from __future__ import annotations

from rules import _register_full_game_triplet, register, Rule

REVIEW = "2026-09-15"
AZ_SOURCE = "https://www.fanduel.com/fanduel-sportsbook-house-rules-az"
CO_SOURCE = "https://www.fanduel.com/fanduel-sportsbook-house-rules-co"
NJ_SOURCE = "https://www.fanduel.com/fanduel-sportsbook-house-rules-nj"
PA_SOURCE = "https://www.fanduel.com/fanduel-sportsbook-house-rules-pa"


def _fanduel_baseball(jurisdiction: str, source: str) -> None:
    register(Rule(
        book="fanduel", sport="baseball", market_family="winner", jurisdiction=jurisdiction,
        reviewed=True, version=REVIEW, source=source,
        settlement_profile="mlb_full_game_2way_action_v1",
        overtime=True, listed_pitcher=False,
        shortened_game_policy="official_after_5_or_4.5_home_leading",
        push_policy="tie_push",
        notes=f"{jurisdiction.upper()} FanDuel house rules reviewed {REVIEW}: 5/4.5 official-game moneyline, extra innings included, 48h resume. Same class as NY/IN.",
    ))
    for family in ("spreads", "totals"):
        register(Rule(
            book="fanduel", sport="baseball", market_family=family, jurisdiction=jurisdiction,
            reviewed=True, version=REVIEW, source=source,
            settlement_profile="mlb_full_game_9_or_8.5_v1",
            overtime=True, listed_pitcher=False,
            shortened_game_policy="9_or_8.5_home_leading_unless_unconditionally_determined",
            push_policy="push",
            notes=f"{jurisdiction.upper()} FanDuel house rules: 9/8.5 run-line/total unless already determined. Same class as NY/IN.",
        ))


def _fanduel_core_sports(jurisdiction: str, source: str) -> None:
    _fanduel_baseball(jurisdiction, source)
    _register_full_game_triplet(
        book="fanduel", sport="americanfootball", jurisdiction=jurisdiction, source=source,
        profile="fd_ny_football_full_game_v1", overtime=True,
        shortened_game_policy="suspended_before_required_time_void_if_not_completed_within_24h;abandoned_or_postponed_60h",
        notes=f"{jurisdiction.upper()} FanDuel house rules reviewed {REVIEW}: 24h completion / 60h abandoned windows match NY football. Periods and props excluded.",
        version=REVIEW,
    )
    _register_full_game_triplet(
        book="fanduel", sport="basketball", jurisdiction=jurisdiction, source=source,
        profile="fd_ny_basketball_full_game_v1", overtime=True,
        shortened_game_policy="nba_or_ncaa_requires_full_regulation_completion_within_24h_unless_market_pre_determined",
        notes=f"{jurisdiction.upper()} FanDuel house rules reviewed {REVIEW}: NBA 48 minutes / NCAA 40 minutes within 24h matches NY basketball. Quarters/props excluded.",
        version=REVIEW,
    )
    _register_full_game_triplet(
        book="fanduel", sport="icehockey", jurisdiction=jurisdiction, source=source,
        profile="fd_ny_hockey_full_game_v1", overtime=True,
        shortened_game_policy="north_american_game_requires_55_minutes_or_resume_within_24h_unless_unequivocally_determined",
        notes=f"{jurisdiction.upper()} FanDuel house rules reviewed {REVIEW}: NA hockey 55 minutes / 24h resume matches NY. Regulation-only markets excluded.",
        version=REVIEW,
    )
    _register_full_game_triplet(
        book="fanduel", sport="soccer", jurisdiction=jurisdiction, source=source,
        profile="fd_ny_soccer_regulation_v1", overtime=False,
        shortened_game_policy="abandoned_undetermined_markets_void_unless_restart_reschedule_conditions_met",
        notes=f"{jurisdiction.upper()} FanDuel house rules reviewed {REVIEW}: 90-minute soccer winner/spread/total. Extra-time/to-qualify excluded.",
        version=REVIEW,
    )
    for family in ("winner", "spreads", "totals"):
        register(Rule(
            book="fanduel", sport="tennis", market_family=family, jurisdiction=jurisdiction,
            reviewed=True, version=REVIEW, source=source,
            settlement_profile="fd_ny_tennis_full_match_v1",
            overtime=None, participation_required=True,
            retirement_policy="after_start_retiring_selection_void;progressing_selection_wins_moneyline_across_grades;other_undetermined_selections_void",
            shortened_game_policy="suspension_bets_stand_if_completed_within_same_tournament;market_specific_retirement_rules_apply",
            push_policy="market_specific",
            notes=f"{jurisdiction.upper()} FanDuel tennis: retiring selection void, progressor wins moneyline. Kept distinct from DraftKings.",
        ))


# AZ baseball/football/basketball were registered in the prior pack; extend AZ
# hockey/soccer/tennis from the same AZ page and add CO/NJ/PA after reading
# those jurisdiction pages.
_fanduel_baseball("az", AZ_SOURCE)
_register_full_game_triplet(
    book="fanduel", sport="americanfootball", jurisdiction="az", source=AZ_SOURCE,
    profile="fd_ny_football_full_game_v1", overtime=True,
    shortened_game_policy="suspended_before_required_time_void_if_not_completed_within_24h;abandoned_or_postponed_60h",
    notes="AZ house rules reviewed 2026-09-15: 24h completion / 60h abandoned windows match NY FanDuel football. Periods and props excluded.",
    version=REVIEW,
)
_register_full_game_triplet(
    book="fanduel", sport="basketball", jurisdiction="az", source=AZ_SOURCE,
    profile="fd_ny_basketball_full_game_v1", overtime=True,
    shortened_game_policy="nba_or_ncaa_requires_full_regulation_completion_within_24h_unless_market_pre_determined",
    notes="AZ house rules reviewed 2026-09-15: NBA 48 minutes within 24h matches NY FanDuel basketball. Quarters/props excluded.",
    version=REVIEW,
)
_register_full_game_triplet(
    book="fanduel", sport="icehockey", jurisdiction="az", source=AZ_SOURCE,
    profile="fd_ny_hockey_full_game_v1", overtime=True,
    shortened_game_policy="north_american_game_requires_55_minutes_or_resume_within_24h_unless_unequivocally_determined",
    notes="AZ house rules reviewed 2026-09-15: NA hockey 55 minutes / 24h resume matches NY FanDuel hockey.",
    version=REVIEW,
)
_register_full_game_triplet(
    book="fanduel", sport="soccer", jurisdiction="az", source=AZ_SOURCE,
    profile="fd_ny_soccer_regulation_v1", overtime=False,
    shortened_game_policy="abandoned_undetermined_markets_void_unless_restart_reschedule_conditions_met",
    notes="AZ house rules reviewed 2026-09-15: 90-minute soccer matches NY FanDuel soccer.",
    version=REVIEW,
)
for _family in ("winner", "spreads", "totals"):
    register(Rule(
        book="fanduel", sport="tennis", market_family=_family, jurisdiction="az",
        reviewed=True, version=REVIEW, source=AZ_SOURCE,
        settlement_profile="fd_ny_tennis_full_match_v1",
        overtime=None, participation_required=True,
        retirement_policy="after_start_retiring_selection_void;progressing_selection_wins_moneyline_across_grades;other_undetermined_selections_void",
        shortened_game_policy="suspension_bets_stand_if_completed_within_same_tournament;market_specific_retirement_rules_apply",
        push_policy="market_specific",
        notes="AZ FanDuel tennis reviewed 2026-09-15: progressor wins moneyline after start; distinct from DraftKings.",
    ))

for _jurisdiction, _source in (("co", CO_SOURCE), ("nj", NJ_SOURCE), ("pa", PA_SOURCE)):
    _fanduel_core_sports(_jurisdiction, _source)
