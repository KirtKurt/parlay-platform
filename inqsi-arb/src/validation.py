"""Canonical settlement validation for normalized provider markets."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List

from rules import compatibility


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
    return key or "unknown"


def market_family(market_key: str) -> str:
    key = str(market_key or "").lower()
    if key in {"h2h", "h2h_3_way", "draw_no_bet"} or key.endswith("_winner"): return "winner"
    if "spread" in key or "handicap" in key: return "spreads"
    if "total" in key: return "totals"
    if key == "outrights" or "championship" in key or "award" in key: return "futures"
    if key.startswith("player_") or key.startswith("batter_") or key.startswith("pitcher_"): return "player_props"
    if key.startswith("team_"): return "team_props"
    if any(x in key for x in ("_h1", "_q1", "_p1", "innings", "set_")): return "periods"
    return "game_props"


def validate_event(event: Dict[str, Any], *, jurisdiction: str = "*") -> Dict[str, Any]:
    row = dict(event)
    books = sorted({str(q.get("book") or "").lower() for q in row.get("quotes", []) if q.get("book")})
    s = sport_family(row.get("sport"))
    m = market_family(row.get("market"))
    result = compatibility(books, s, m, jurisdiction)
    status_map = {"COMPATIBLE": "compatible", "INCOMPATIBLE": "incompatible", "UNKNOWN": "unknown"}
    row["rules_status"] = status_map.get(result.get("status"), "unknown")
    context = dict(row.get("context") or {})
    context["settlement_validation"] = result
    context["sport_family"] = s
    context["market_family"] = m
    row["context"] = context
    return row


def validate_events(events: Iterable[Dict[str, Any]], *, jurisdiction: str = "*") -> List[Dict[str, Any]]:
    return [validate_event(e, jurisdiction=jurisdiction) for e in events or []]
