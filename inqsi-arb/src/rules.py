"""Versioned fail-closed settlement compatibility registry for Inqsi ARB.

Only explicitly reviewed rules can yield COMPATIBLE. Unknown book/sport/market
combinations remain UNKNOWN and therefore cannot be labelled verified arbs.
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


# Narrow reviewed seeds from current official house rules. These do not imply
# compatibility for listed-pitcher, 3-way, alternate, period, prop, or other
# unreviewed variants; those continue to fail closed.
_DK_BASEBALL = "https://sportsbook.draftkings.com/help/sport-rules/baseball"
_FD_BASEBALL = "https://www.fanduel.com/fanduel-sportsbook-house-rules-in"

for _book, _source in (("draftkings", _DK_BASEBALL), ("fanduel", _FD_BASEBALL)):
    register(Rule(
        book=_book, sport="baseball", market_family="winner", reviewed=True,
        version="2026-09-11", source=_source,
        settlement_profile="mlb_full_game_2way_action_v1",
        overtime=True, listed_pitcher=False,
        shortened_game_policy="official_after_5_or_4.5_home_leading",
        push_policy="tie_push",
        notes="Default full-game 2-way MLB moneyline only; explicitly listed-pitcher and 3-way variants require separate rules.",
    ))
    register(Rule(
        book=_book, sport="baseball", market_family="spreads", reviewed=True,
        version="2026-09-11", source=_source,
        settlement_profile="mlb_full_game_9_or_8.5_v1",
        overtime=True, listed_pitcher=False,
        shortened_game_policy="9_or_8.5_home_leading_unless_unconditionally_determined",
        push_policy="push",
        notes="Default full-game MLB run-line family; alternates/periods are not implied by this rule.",
    ))
    register(Rule(
        book=_book, sport="baseball", market_family="totals", reviewed=True,
        version="2026-09-11", source=_source,
        settlement_profile="mlb_full_game_9_or_8.5_v1",
        overtime=True, listed_pitcher=False,
        shortened_game_policy="9_or_8.5_home_leading_unless_unconditionally_determined",
        push_policy="push",
        notes="Default full-game MLB total family; alternates/periods are not implied by this rule.",
    ))
