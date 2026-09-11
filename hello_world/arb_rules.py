"""Fail-closed sportsbook and market settlement compatibility for Inqsi ARB.

This module deliberately defaults unknown combinations to UNKNOWN.  A caller may
show an unknown candidate for research, but it must not label it as a verified
arb until compatible rules are registered.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, Iterable, Optional, Tuple

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
    overtime: Optional[bool] = None
    listed_pitcher: Optional[bool] = None
    participation_required: Optional[bool] = None
    retirement_policy: Optional[str] = None
    shortened_game_policy: Optional[str] = None
    notes: str = ""

# Conservative seed registry.  These entries describe families whose basic
# grading semantics are broadly stable; exact operator-specific exceptions can
# be layered in without changing engine code.
_RULES: Dict[Tuple[str, str, str, str], Rule] = {}


def register(rule: Rule) -> None:
    key = (rule.book.lower(), rule.sport.lower(), rule.market_family.lower(), rule.jurisdiction.lower())
    _RULES[key] = rule


def lookup(book: str, sport: str, market_family: str, jurisdiction: str = "*") -> Optional[Rule]:
    b, s, m, j = book.lower(), sport.lower(), market_family.lower(), jurisdiction.lower()
    return _RULES.get((b, s, m, j)) or _RULES.get((b, s, m, "*")) or _RULES.get(("*", s, m, j)) or _RULES.get(("*", s, m, "*"))


def compatibility(books: Iterable[str], sport: str, market_family: str, jurisdiction: str = "*") -> Dict[str, Any]:
    found = []
    missing = []
    for book in sorted(set(str(b).lower() for b in books if b)):
        rule = lookup(book, sport, market_family, jurisdiction)
        if rule is None:
            missing.append(book)
        else:
            found.append(asdict(rule))
    if missing:
        return {"status": UNKNOWN, "missing_books": missing, "rules": found}
    # If all known rules disagree on a material settlement dimension, fail.
    material = ("overtime", "listed_pitcher", "participation_required", "retirement_policy", "shortened_game_policy")
    for field in material:
        values = {r.get(field) for r in found if r.get(field) is not None}
        if len(values) > 1:
            return {"status": INCOMPATIBLE, "field": field, "values": sorted(str(v) for v in values), "rules": found}
    return {"status": COMPATIBLE, "rules": found}

# Generic same-family seeds. Unknown operator-specific exceptions still remain
# fail-closed because explicit book rules can override these.
for _sport in ("baseball", "basketball", "americanfootball", "icehockey", "soccer", "tennis"):
    for _market in ("h2h", "moneyline", "winner", "spreads", "totals"):
        register(Rule(book="*", sport=_sport, market_family=_market, notes="generic family seed; operator exceptions override"))
