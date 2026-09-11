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
    # Exact book always required. Sport/market wildcard is allowed only when the
    # reviewed rule itself explicitly declares it.
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
    return {"status": COMPATIBLE, "rules": found, "missing_books": []}


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
