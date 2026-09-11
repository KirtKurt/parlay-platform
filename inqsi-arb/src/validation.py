"""Canonical settlement and freshness validation for normalized markets.

A valid reviewed book pair must not be poisoned by unrelated unreviewed books.
Quotes are partitioned by reviewed settlement profile and each compatible group
is evaluated independently. Stale or untimestamped quotes are never eligible
for verified arbitrage qualification.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from typing import Any, Dict, Iterable, List

from freshness import assess_quote
from rules import UNKNOWN, compatibility, lookup
import rules_fanatics  # noqa: F401  # reviewed supplemental registrations


def sport_family(sport_key: str) -> str:
    key = str(sport_key or "").lower()
    if "baseball" in key: return "baseball"
    if "basketball" in key: return "basketball"
    if "football" in key and "soccer" not in key: return "americanfootball"
    if "hockey" in key: return "icehockey"
    if "soccer" in key: return "soccer"
    if "tennis" in key: return "tennis"
    if "mma" in key or "ufc" in key: return "mma"
    if "boxing" in key: return "boxing"
    if "cricket" in key: return "cricket"
    if "rugby" in key: return "rugby"
    if "golf" in key: return "golf"
    if "aussierules" in key or "afl" in key: return "aussierules"
    if "lacrosse" in key: return "lacrosse"
    if "volleyball" in key: return "volleyball"
    if "handball" in key: return "handball"
    if "tabletennis" in key or "table_tennis" in key: return "tabletennis"
    return key or "unknown"


def market_family(market_key: str) -> str:
    key = str(market_key or "").lower()
    if any(x in key for x in ("_h1", "_h2", "_q1", "_q2", "_q3", "_q4", "_p1", "_p2", "_p3", "innings", "_s1", "_s2", "set_")):
        return "periods"
    if key.startswith("player_") or key.startswith("batter_") or key.startswith("pitcher_"): return "player_props"
    if key.startswith("team_") or key.startswith("alternate_team_"): return "team_props"
    if key == "outrights" or key == "outrights_lay" or "championship" in key or "award" in key: return "futures"
    if key in {"h2h", "h2h_3_way", "draw_no_bet"} or key.endswith("_winner"): return "winner"
    if "spread" in key or "handicap" in key: return "spreads"
    if "total" in key: return "totals"
    return "game_props"


def _annotate(row: Dict[str, Any], result: Dict[str, Any], s: str, m: str, unknown_books: List[str], freshness_evidence: List[Dict[str, Any]]) -> Dict[str, Any]:
    out = dict(row)
    status_map = {"COMPATIBLE": "compatible", "INCOMPATIBLE": "incompatible", "UNKNOWN": "unknown"}
    out["rules_status"] = status_map.get(result.get("status"), "unknown")
    context = dict(out.get("context") or {})
    context["settlement_validation"] = result
    context["sport_family"] = s
    context["market_family"] = m
    context["excluded_unreviewed_books"] = sorted(set(unknown_books))
    excluded = [e for e in freshness_evidence if not e.get("fresh")]
    context["quote_freshness"] = {
        "eligible_quotes": len(freshness_evidence) - len(excluded),
        "excluded_quotes": len(excluded),
        "excluded": excluded[:50],
    }
    out["context"] = context
    return out


def validate_event(event: Dict[str, Any], *, jurisdiction: str = "*") -> List[Dict[str, Any]]:
    row = dict(event)
    raw_quotes = list(row.get("quotes") or [])
    s = sport_family(row.get("sport"))
    m = market_family(row.get("market"))

    fresh_quotes: List[Dict[str, Any]] = []
    freshness_evidence: List[Dict[str, Any]] = []
    for quote in raw_quotes:
        q = dict(quote)
        assessment = assess_quote(q)
        evidence = {
            "book": str(q.get("book") or "").strip().lower(),
            "outcome": q.get("outcome"),
            "last_update": q.get("last_update"),
            **assessment,
        }
        freshness_evidence.append(evidence)
        if assessment["fresh"]:
            fresh_quotes.append(q)

    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    unknown_books: List[str] = []
    rule_by_book: Dict[str, Any] = {}
    for quote in fresh_quotes:
        book = str(quote.get("book") or "").strip().lower()
        if not book:
            continue
        rule = rule_by_book.get(book)
        if rule is None:
            rule = lookup(book, s, m, jurisdiction)
            rule_by_book[book] = rule
        if rule is None or not rule.reviewed or not rule.settlement_profile:
            unknown_books.append(book)
            continue
        groups[rule.settlement_profile].append(dict(quote))

    validated: List[Dict[str, Any]] = []
    for profile, group_quotes in sorted(groups.items()):
        books = sorted({str(q.get("book") or "").lower() for q in group_quotes if q.get("book")})
        result = compatibility(books, s, m, jurisdiction)
        result["settlement_profile"] = profile
        grouped = dict(row)
        grouped["id"] = f"{row.get('id') or row.get('market_id') or ''}|rules:{profile}"
        grouped["quotes"] = group_quotes
        validated.append(_annotate(grouped, result, s, m, unknown_books, freshness_evidence))

    if validated:
        return validated

    fresh_books = sorted({str(q.get("book") or "").lower() for q in fresh_quotes if q.get("book")})
    if not fresh_quotes:
        result = {
            "status": UNKNOWN,
            "reason": "QUOTE_FRESHNESS_NOT_ESTABLISHED",
            "missing_books": sorted({str(q.get("book") or "").lower() for q in raw_quotes if q.get("book")}),
            "rules": [],
        }
    else:
        result = compatibility(fresh_books, s, m, jurisdiction)
    result["rules_considered"] = [asdict(r) for r in rule_by_book.values() if r is not None]
    filtered = dict(row)
    filtered["quotes"] = fresh_quotes
    return [_annotate(filtered, result, s, m, unknown_books or fresh_books, freshness_evidence)]


def validate_events(events: Iterable[Dict[str, Any]], *, jurisdiction: str = "*") -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for event in events or []:
        out.extend(validate_event(event, jurisdiction=jurisdiction))
    return out
