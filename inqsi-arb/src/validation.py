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
import rules_bootstrap  # noqa: F401  # canonical supplemental registrations


_NORTH_AMERICAN_HOCKEY_PREFIXES = (
    "icehockey_nhl", "icehockey_ahl", "icehockey_echl", "icehockey_ncaa",
    "icehockey_usa", "icehockey_canada",
)
_NBA_NCAA_BASKETBALL_PREFIXES = (
    "basketball_nba", "basketball_wnba", "basketball_ncaab", "basketball_ncaaw",
)


def _rule_matches_exact_sport(rule: Any, sport: str, exact_sport: str) -> bool:
    """Keep regional hockey policies from qualifying unrelated leagues."""
    policy = str(getattr(rule, "shortened_game_policy", "") or "").lower()
    if sport == "icehockey":
        region_scoped = "north_american" in policy or "us_pro_55" in policy
        explicitly_non_na = "non_na_" in policy or "non_us_" in policy
        return (
            not region_scoped
            or exact_sport.startswith(_NORTH_AMERICAN_HOCKEY_PREFIXES)
            or explicitly_non_na
        )
    if sport == "basketball" and "nba_or_ncaa" in policy:
        return exact_sport.startswith(_NBA_NCAA_BASKETBALL_PREFIXES)
    return True


def sport_family(sport_key: str) -> str:
    key = str(sport_key or "").lower()
    if key == "baseball_mlb": return "baseball"
    if "baseball" in key: return key or "baseball_unknown"
    if "basketball" in key: return "basketball"
    if "football" in key and "soccer" not in key: return "americanfootball"
    if "hockey" in key: return "icehockey"
    if "soccer" in key: return "soccer"
    if "tabletennis" in key or "table_tennis" in key: return "tabletennis"
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
    return key or "unknown"


def market_family(market_key: str) -> str:
    """Broad discovery/UI family.

    Period markets intentionally remain grouped here for catalog/discovery use.
    Settlement qualification uses ``settlement_market_family`` below so a
    reviewed two-way inning winner rule can never silently qualify a three-way
    winner, run line, total, or team total from the same period.
    """
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


def settlement_market_family(market_key: str) -> str:
    """Return the narrow family used by the settlement-rule trust boundary.

    The provider exposes many period keys under a common discovery category, but
    their settlement semantics are materially different. In particular, a
    two-way inning/grouped winner can void a tie while a three-way market settles
    the tie as a winner. Spreads, totals, and team totals also require their own
    reviewed rows. Unknown variants remain fail-closed.
    """
    key = str(market_key or "").lower()
    broad = market_family(key)
    if broad != "periods":
        if key == "h2h":
            return "winner"
        if key == "h2h_3_way":
            return "winner_3way"
        if key == "draw_no_bet":
            return "draw_no_bet"
        if broad == "winner":
            return "winner_variant"
        return broad
    if "h2h_3_way" in key:
        return "period_winner_3way"
    if key.startswith("h2h") or key.endswith("_winner"):
        return "period_winner_2way"
    if "team_total" in key:
        return "period_team_totals"
    if "spread" in key or "handicap" in key:
        return "period_spreads"
    if "total" in key:
        return "period_totals"
    return "period_game_props"


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
    m = settlement_market_family(row.get("market"))
    exact_sport = str(row.get("sport") or "").strip().lower()

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
            if (rule is not None and s == "baseball" and exact_sport != "baseball_mlb"
                    and str(rule.settlement_profile or "").startswith("mlb_")):
                rule = None
            if rule is not None and not _rule_matches_exact_sport(rule, s, exact_sport):
                rule = None
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

    if validated and (unknown_books or len(groups) > 1):
        # Preserve visibility into a better mathematical price that depends on
        # an unreviewed book or crosses materially different rule profiles.
        # This row can only be held/rejected; it can never qualify as verified.
        combined = dict(row)
        combined["id"] = f"{row.get('id') or row.get('market_id') or ''}|rules:unverified"
        combined["quotes"] = fresh_quotes
        all_books = sorted({str(q.get("book") or "").lower() for q in fresh_quotes if q.get("book")})
        combined_result = compatibility(all_books, s, m, jurisdiction)
        combined_result["rules_considered"] = [asdict(r) for r in rule_by_book.values() if r is not None]
        validated.append(_annotate(
            combined, combined_result, s, m, unknown_books, freshness_evidence
        ))

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
    elif unknown_books and not groups:
        result = {
            "status": UNKNOWN,
            "reason": "EVENT_OR_MARKET_SCOPE_UNREVIEWED",
            "missing_books": sorted(set(unknown_books)),
            "rules": [],
        }
    else:
        result = compatibility(fresh_books, s, m, jurisdiction)
    filtered = dict(row)
    filtered["quotes"] = fresh_quotes
    result["rules_considered"] = [asdict(r) for r in rule_by_book.values() if r is not None]
    return [_annotate(filtered, result, s, m, unknown_books or fresh_books, freshness_evidence)]


def validate_events(events: Iterable[Dict[str, Any]], *, jurisdiction: str = "*") -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for event in events or []:
        out.extend(validate_event(event, jurisdiction=jurisdiction))
    return out
