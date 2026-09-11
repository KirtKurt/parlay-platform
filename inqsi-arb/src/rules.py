"""Versioned fail-closed settlement compatibility registry for Inqsi ARB.

Only explicitly reviewed rules can yield COMPATIBLE. Unknown book/sport/market
combinations remain UNKNOWN and therefore cannot be labelled verified arbs.
Jurisdiction-specific rules are never generalized to other jurisdictions.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

COMPATIBLE = "COMPATIBLE"
INCOMPATIBLE = "INCOMPATIBLE"
UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Rule:
    book: str
    sport: str
    market_family: str
    jurisdiction: str = "*"
    version: str = "1"
    reviewed: bool = False
    source: str = ""
    settlement_profile: str = ""
    overtime: Optional[bool] = None
    listed_pitcher: Optional[bool] = None
    participation_required: Optional[bool] = None
    retirement_policy: Optional[str] = None
    shortened_game_policy: Optional[str] = None
    push_policy: Optional[str] = None
    notes: str = ""


_RULES: Dict[Tuple[str, str, str, str], Rule] = {}


def _norm(value: str) -> str:
    return str(value or "").strip().lower()


def register(rule: Rule) -> None:
    _RULES[(_norm(rule.book), _norm(rule.sport), _norm(rule.market_family), _norm(rule.jurisdiction))] = rule


def lookup(book: str, sport: str, market_family: str, jurisdiction: str = "*") -> Optional[Rule]:
    b, s, m, j = map(_norm, (book, sport, market_family, jurisdiction))
    for key in ((b, s, m, j), (b, s, m, "*"), (b, "*", m, j), (b, "*", m, "*")):
        rule = _RULES.get(key)
        if rule is not None:
            return rule
    return None


def compatibility(books: Iterable[str], sport: str, market_family: str, jurisdiction: str = "*") -> Dict[str, Any]:
    unique = sorted({_norm(b) for b in books if _norm(b)})
    if not unique:
        return {"status": UNKNOWN, "missing_books": [], "rules": [], "reason": "NO_BOOKS"}
    found: List[Dict[str, Any]] = []
    missing: List[str] = []
    for book in unique:
        rule = lookup(book, sport, market_family, jurisdiction)
        if rule is None or not rule.reviewed:
            missing.append(book)
        else:
            found.append(asdict(rule))
    if missing:
        return {"status": UNKNOWN, "missing_books": missing, "rules": found, "reason": "UNREVIEWED_OR_MISSING_RULE"}
    profiles = {r.get("settlement_profile") for r in found if r.get("settlement_profile")}
    if len(profiles) > 1:
        return {"status": INCOMPATIBLE, "field": "settlement_profile", "values": sorted(profiles), "rules": found, "reason": "SETTLEMENT_PROFILE_CONFLICT"}
    material = (
        "overtime", "listed_pitcher", "participation_required", "retirement_policy",
        "shortened_game_policy", "push_policy",
    )
    for field in material:
        values = {r.get(field) for r in found if r.get(field) is not None}
        if len(values) > 1:
            return {
                "status": INCOMPATIBLE, "field": field,
                "values": sorted(str(v) for v in values), "rules": found,
                "reason": "SETTLEMENT_RULE_CONFLICT",
            }
    return {"status": COMPATIBLE, "rules": found, "missing_books": [], "settlement_profile": next(iter(profiles), "")}


def coverage(books: Iterable[str], sport: str, market_family: str, jurisdiction: str = "*") -> Dict[str, Any]:
    result = compatibility(books, sport, market_family, jurisdiction)
    return {
        "sport": sport,
        "market_family": market_family,
        "jurisdiction": jurisdiction,
        "status": result["status"],
        "reviewed_books": sorted(r["book"] for r in result.get("rules", [])),
        "missing_books": result.get("missing_books", []),
    }


def registry_size() -> int:
    return len(_RULES)


def registry_rows() -> List[Dict[str, Any]]:
    return [asdict(_RULES[k]) for k in sorted(_RULES)]


REVIEW_DATE = "2026-09-11"
_DK_BASEBALL = "https://sportsbook.draftkings.com/help/sport-rules/baseball"
_DK_FOOTBALL = "https://sportsbook.draftkings.com/help/sport-rules/football"
_DK_BASKETBALL = "https://sportsbook.draftkings.com/help/sport-rules/basketball"
_DK_HOCKEY = "https://sportsbook.draftkings.com/help/sport-rules/hockey"
_DK_TENNIS = "https://sportsbook.draftkings.com/help/sport-rules/tennis"
_DK_SOCCER = "https://sportsbook.draftkings.com/help/sport-rules/soccer"
_FD_IN = "https://www.fanduel.com/fanduel-sportsbook-house-rules-in"
_FD_NY = "https://www.fanduel.com/fanduel-sportsbook-house-rules-ny"


def _register_full_game_triplet(
    *,
    book: str,
    sport: str,
    jurisdiction: str,
    source: str,
    profile: str,
    overtime: bool,
    shortened_game_policy: str,
    push_policy: str = "push",
    notes: str,
) -> None:
    for family in ("winner", "spreads", "totals"):
        register(Rule(
            book=book,
            sport=sport,
            market_family=family,
            jurisdiction=jurisdiction,
            reviewed=True,
            version=REVIEW_DATE,
            source=source,
            settlement_profile=profile,
            overtime=overtime,
            participation_required=False,
            shortened_game_policy=shortened_game_policy,
            push_policy=push_policy,
            notes=notes,
        ))


# DraftKings publishes general sport rules rather than a state-specific sports
# rule page. We retain only narrow, full-game families. Periods, props, futures,
# 3-way, listed-pitcher and other variants require their own registry rows.
register(Rule(
    book="draftkings", sport="baseball", market_family="winner", reviewed=True,
    version=REVIEW_DATE, source=_DK_BASEBALL,
    settlement_profile="mlb_full_game_2way_action_v1",
    overtime=True, listed_pitcher=False,
    shortened_game_policy="official_after_5_or_4.5_home_leading",
    push_policy="tie_push",
    notes="Default full-game 2-way MLB moneyline only; listed-pitcher and 3-way variants are excluded.",
))
for _family in ("spreads", "totals"):
    register(Rule(
        book="draftkings", sport="baseball", market_family=_family, reviewed=True,
        version=REVIEW_DATE, source=_DK_BASEBALL,
        settlement_profile="mlb_full_game_9_or_8.5_v1",
        overtime=True, listed_pitcher=False,
        shortened_game_policy="9_or_8.5_home_leading_unless_unconditionally_determined",
        push_policy="push",
        notes="Default full-game MLB run-line/total family only; periods and props are excluded.",
    ))

# FanDuel house rules are jurisdiction-specific. The previous wildcard mapping
# used an Indiana source for every jurisdiction; that was too broad. Keep exact
# Indiana coverage and add exact New York coverage from the current NY house rules.
for _jurisdiction, _source in (("in", _FD_IN), ("ny", _FD_NY)):
    register(Rule(
        book="fanduel", sport="baseball", market_family="winner", jurisdiction=_jurisdiction,
        reviewed=True, version=REVIEW_DATE, source=_source,
        settlement_profile="mlb_full_game_2way_action_v1",
        overtime=True, listed_pitcher=False,
        shortened_game_policy="official_after_5_or_4.5_home_leading",
        push_policy="tie_push",
        notes="Full-game 2-way MLB moneyline under the jurisdiction house rules; listed-pitcher variants are excluded.",
    ))
    for _family in ("spreads", "totals"):
        register(Rule(
            book="fanduel", sport="baseball", market_family=_family, jurisdiction=_jurisdiction,
            reviewed=True, version=REVIEW_DATE, source=_source,
            settlement_profile="mlb_full_game_9_or_8.5_v1",
            overtime=True, listed_pitcher=False,
            shortened_game_policy="9_or_8.5_home_leading_unless_unconditionally_determined",
            push_policy="push",
            notes="Full-game MLB run-line/total under the jurisdiction house rules; periods and props are excluded.",
        ))

# New York full-game coverage. Distinct settlement profiles are intentional when
# interruption/retirement rules differ between books. This makes those book pairs
# explicitly INCOMPATIBLE instead of incorrectly calling a mathematical arb safe.
_register_full_game_triplet(
    book="draftkings", sport="americanfootball", jurisdiction="ny", source=_DK_FOOTBALL,
    profile="dk_ny_football_full_game_v1", overtime=True,
    shortened_game_policy="interrupted_full_game_void_if_not_naturally_concluded_within_48h_unless_unconditionally_determined",
    notes="Full-game 2-way football winner/spread/total only; period and player markets are excluded.",
)
_register_full_game_triplet(
    book="fanduel", sport="americanfootball", jurisdiction="ny", source=_FD_NY,
    profile="fd_ny_football_full_game_v1", overtime=True,
    shortened_game_policy="suspended_before_required_time_void_if_not_completed_within_24h;abandoned_or_postponed_60h",
    notes="New York full-game football winner/spread/total only; period and player markets are excluded.",
)

_register_full_game_triplet(
    book="draftkings", sport="basketball", jurisdiction="ny", source=_DK_BASKETBALL,
    profile="dk_ny_basketball_full_game_v1", overtime=True,
    shortened_game_policy="interrupted_full_game_void_if_not_naturally_concluded_within_48h_unless_unconditionally_determined",
    notes="Full-game basketball winner/spread/total only; quarter/half and player markets are excluded.",
)
_register_full_game_triplet(
    book="fanduel", sport="basketball", jurisdiction="ny", source=_FD_NY,
    profile="fd_ny_basketball_full_game_v1", overtime=True,
    shortened_game_policy="nba_or_ncaa_requires_full_regulation_completion_within_24h_unless_market_pre_determined",
    notes="New York NBA/NCAA/WNBA full-game winner/spread/total only; quarter/half and player markets are excluded.",
)

_register_full_game_triplet(
    book="draftkings", sport="icehockey", jurisdiction="ny", source=_DK_HOCKEY,
    profile="dk_ny_hockey_full_game_v1", overtime=True,
    shortened_game_policy="interrupted_full_game_void_if_not_naturally_concluded_within_48h_unless_unconditionally_determined",
    notes="Full-game hockey winner/spread/total including OT and standard shootout settlement; regulation-only markets are excluded.",
)
_register_full_game_triplet(
    book="fanduel", sport="icehockey", jurisdiction="ny", source=_FD_NY,
    profile="fd_ny_hockey_full_game_v1", overtime=True,
    shortened_game_policy="north_american_game_requires_55_minutes_or_resume_within_24h_unless_unequivocally_determined",
    notes="New York North-American hockey full-game moneyline/puck-line/total including OT and shootout; regulation markets are excluded.",
)

_register_full_game_triplet(
    book="draftkings", sport="soccer", jurisdiction="ny", source=_DK_SOCCER,
    profile="dk_ny_soccer_regulation_v1", overtime=False,
    shortened_game_policy="regulation_match_must_naturally_conclude_except_documented_friendly_interruption_rules",
    notes="Standard regulation-time soccer winner/spread/total only; extra-time, to-qualify, period and prop markets are excluded.",
)
_register_full_game_triplet(
    book="fanduel", sport="soccer", jurisdiction="ny", source=_FD_NY,
    profile="fd_ny_soccer_regulation_v1", overtime=False,
    shortened_game_policy="abandoned_undetermined_markets_void_unless_restart_reschedule_conditions_met",
    notes="New York standard 90-minute soccer winner/spread/total only; extra-time, to-qualify, period and prop markets are excluded.",
)

# Tennis retirement rules differ materially by book/tour, so the profiles are
# deliberately distinct and never cross-qualified at the broad tennis-family level.
for _family in ("winner", "spreads", "totals"):
    register(Rule(
        book="draftkings", sport="tennis", market_family=_family, jurisdiction="ny",
        reviewed=True, version=REVIEW_DATE, source=_DK_TENNIS,
        settlement_profile="dk_ny_tennis_full_match_v1",
        overtime=None, participation_required=True,
        retirement_policy="tour_dependent;major_atp_wta_challenger_davis_bjk_united_olympic_moneyline_progressor_wins_others_void;itf_incomplete_void",
        shortened_game_policy="must_reach_natural_end_unless_unconditionally_determined_or_market_specific_retirement_rule",
        push_policy="market_specific",
        notes="Broad tennis family is retained as a distinct DraftKings-only profile because retirement rules are tour dependent; no cross-book verification is implied.",
    ))
    register(Rule(
        book="fanduel", sport="tennis", market_family=_family, jurisdiction="ny",
        reviewed=True, version=REVIEW_DATE, source=_FD_NY,
        settlement_profile="fd_ny_tennis_full_match_v1",
        overtime=None, participation_required=True,
        retirement_policy="after_start_retiring_selection_void;progressing_selection_wins_moneyline_across_grades;other_undetermined_selections_void",
        shortened_game_policy="suspension_bets_stand_if_completed_within_same_tournament;market_specific_retirement_rules_apply",
        push_policy="market_specific",
        notes="New York tennis rule profile; kept distinct from DraftKings due materially different retirement treatment, especially lower-tier matches.",
    ))
