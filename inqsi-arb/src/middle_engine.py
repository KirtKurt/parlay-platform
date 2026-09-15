"""Cross-line middle detection for Inqsi ARB.

A middle is a line gap, not a same-contract surebet. This module never places
bets. Risk middles are never labelled arb=true. Free middles (implied sum < 1
across the gapped lines) are mathematical locks with extra upside if the gap
hits; they still require reviewed settlement compatibility to verify, which
this detector does not grant.
"""
from __future__ import annotations

import re
from math import isfinite
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from arb_engine import _net_decimal, _quote_decimal

_POINT_RE = re.compile(r"([+-]?\d+(?:\.\d+)?)\s*$")
MAX_MIDDLES = 100


def _norm_books(raw: Any) -> Optional[set[str]]:
    if not raw:
        return None
    if isinstance(raw, str):
        values = [part.strip().lower() for part in raw.split(",")]
    else:
        values = [str(part).strip().lower() for part in raw]
    allowed = {part for part in values if part}
    return allowed or None


def _event_key(item: Mapping[str, Any]) -> str:
    return str(item.get("event_id") or item.get("event") or item.get("id") or "").strip()


def _family(market: str) -> Optional[str]:
    key = str(market or "").lower()
    if key.endswith("_lay"):
        return None
    if "spread" in key or "handicap" in key:
        return "spread"
    if "total" in key or key.startswith("player_") or key.startswith("batter_") or key.startswith("pitcher_"):
        return "total"
    return None


def _selection(quote: Mapping[str, Any]) -> str:
    desc = str(quote.get("description") or "").strip()
    if desc:
        return desc.lower()
    outcome = str(quote.get("outcome") or "")
    if "::" in outcome:
        return outcome.split("::", 1)[0].strip().lower()
    return ""


def _point_and_side(quote: Mapping[str, Any], family: str) -> Optional[Tuple[str, float]]:
    outcome = str(quote.get("name") or quote.get("outcome") or "").strip()
    point = quote.get("point")
    try:
        point_f = float(point) if point is not None and str(point) != "" else None
    except (TypeError, ValueError):
        point_f = None
    if point_f is None:
        match = _POINT_RE.search(outcome.replace("::", " "))
        if not match:
            return None
        point_f = float(match.group(1))
    if not isfinite(point_f):
        return None
    lower = outcome.lower()
    if family == "total":
        if "over" in lower:
            return "over", point_f
        if "under" in lower:
            return "under", point_f
        return None
    side = _POINT_RE.sub("", outcome.split("::")[-1]).strip(" :+-\t")
    if not side:
        return None
    return side.lower(), point_f


def _best_quotes(events: Iterable[Mapping[str, Any]], allowed: Optional[set[str]]) -> Dict[Tuple[str, str, str], List[Dict[str, Any]]]:
    grouped: Dict[Tuple[str, str, str], Dict[Tuple[str, str, float], Dict[str, Any]]] = {}
    for item in events or []:
        market = str(item.get("market") or "")
        family = _family(market)
        if family is None:
            continue
        event_key = _event_key(item)
        if not event_key:
            continue
        for raw in item.get("quotes") or []:
            book = str(raw.get("book") or "").strip().lower()
            if not book or (allowed and book not in allowed):
                continue
            parsed = _point_and_side(raw, family)
            if parsed is None:
                continue
            side, point = parsed
            try:
                decimal = _quote_decimal(raw)
                net = _net_decimal(decimal, float(raw.get("commission_rate") or 0.0))
            except (TypeError, ValueError):
                continue
            if net <= 1:
                continue
            selection = _selection(raw)
            bucket = grouped.setdefault((event_key, family, selection), {})
            key = (book, side, point)
            candidate = {
                "event": str(item.get("event") or event_key),
                "event_id": str(item.get("event_id") or ""),
                "market": market,
                "commence_time": item.get("commence_time"),
                "family": family,
                "selection": selection,
                "side": side,
                "point": point,
                "book": book,
                "outcome": str(raw.get("outcome") or f"{side} {point}"),
                "american": raw.get("american"),
                "decimal": decimal,
                "net_decimal": net,
                "last_update": raw.get("last_update"),
                "provider": raw.get("provider"),
                "link": raw.get("link"),
                "limit": raw.get("limit"),
            }
            current = bucket.get(key)
            if current is None or net > current["net_decimal"]:
                bucket[key] = candidate
    return {key: list(rows.values()) for key, rows in grouped.items()}


def _stake_plan(over: Mapping[str, Any], under: Mapping[str, Any], bankroll: float) -> Dict[str, Any]:
    implied = (1.0 / over["net_decimal"]) + (1.0 / under["net_decimal"])
    if implied <= 0:
        return {"implied_sum": implied, "legs": []}
    stakes = {
        "over": bankroll * (1.0 / over["net_decimal"]) / implied,
        "under": bankroll * (1.0 / under["net_decimal"]) / implied,
    }
    legs = []
    for key, quote in (("over", over), ("under", under)):
        stake = round(stakes[key], 2)
        payout = stake * quote["net_decimal"]
        legs.append({
            "outcome": quote["outcome"],
            "side": quote["side"],
            "point": quote["point"],
            "book": quote["book"],
            "american": quote.get("american"),
            "decimal": round(quote["decimal"], 6),
            "net_decimal": round(quote["net_decimal"], 6),
            "stake": stake,
            "payout_if_wins": round(payout, 2),
            "last_update": quote.get("last_update"),
            "provider": quote.get("provider"),
            "link": quote.get("link"),
            "limit": quote.get("limit"),
        })
    total = round(sum(leg["stake"] for leg in legs), 2)
    over_only = round(legs[0]["payout_if_wins"] - total, 2)
    under_only = round(legs[1]["payout_if_wins"] - total, 2)
    both = round(legs[0]["payout_if_wins"] + legs[1]["payout_if_wins"] - total, 2)
    return {
        "implied_sum": implied,
        "allocated_stake": total,
        "pnl_if_over_only": over_only,
        "pnl_if_under_only": under_only,
        "pnl_if_middle_hits": both,
        "minimum_miss_pnl": min(over_only, under_only),
        "legs": legs,
    }


def _spread_plan(left: Mapping[str, Any], right: Mapping[str, Any], bankroll: float) -> Dict[str, Any]:
    implied = (1.0 / left["net_decimal"]) + (1.0 / right["net_decimal"])
    if implied <= 0:
        return {"implied_sum": implied, "legs": []}
    quotes = (left, right)
    stakes = [bankroll * (1.0 / q["net_decimal"]) / implied for q in quotes]
    legs = []
    for quote, stake_raw in zip(quotes, stakes):
        stake = round(stake_raw, 2)
        payout = stake * quote["net_decimal"]
        legs.append({
            "outcome": quote["outcome"],
            "side": quote["side"],
            "point": quote["point"],
            "book": quote["book"],
            "american": quote.get("american"),
            "decimal": round(quote["decimal"], 6),
            "net_decimal": round(quote["net_decimal"], 6),
            "stake": stake,
            "payout_if_wins": round(payout, 2),
            "last_update": quote.get("last_update"),
            "provider": quote.get("provider"),
            "link": quote.get("link"),
            "limit": quote.get("limit"),
        })
    total = round(sum(leg["stake"] for leg in legs), 2)
    first_only = round(legs[0]["payout_if_wins"] - total, 2)
    second_only = round(legs[1]["payout_if_wins"] - total, 2)
    both = round(legs[0]["payout_if_wins"] + legs[1]["payout_if_wins"] - total, 2)
    return {
        "implied_sum": implied,
        "allocated_stake": total,
        "pnl_if_first_only": first_only,
        "pnl_if_second_only": second_only,
        "pnl_if_middle_hits": both,
        "minimum_miss_pnl": min(first_only, second_only),
        "legs": legs,
    }


def _row(*, market_id: str, kind: str, family: str, gap: float, plan: Mapping[str, Any],
         event: str, market: str, commence_time: Any, selection: str) -> Dict[str, Any]:
    implied = float(plan.get("implied_sum") or 0.0)
    free = implied > 0 and implied < 1.0
    margin = (1.0 / implied - 1.0) if implied > 0 else -1.0
    return {
        "market_id": market_id,
        "event": event,
        "market": market,
        "commence_time": commence_time,
        "selection": selection,
        "family": family,
        "kind": "free_middle" if free else "risk_middle",
        "opportunity_type": "middle",
        "gap": round(gap, 6),
        "math_arb": bool(free),
        "arb": False,
        "places_bets": False,
        "sum_implied": round(implied, 8),
        "margin_pct": round(margin * 100.0, 4) if free else None,
        "allocated_stake": plan.get("allocated_stake"),
        "pnl_if_middle_hits": plan.get("pnl_if_middle_hits"),
        "minimum_miss_pnl": plan.get("minimum_miss_pnl"),
        "validation": {
            "rules_status": "not_evaluated",
            "rules_compatible": False,
            "qualification_reason": "MIDDLE_REQUIRES_CROSS_LINE_SETTLEMENT_REVIEW",
        },
        "legs": list(plan.get("legs") or []),
        "kind_detail": kind,
    }


def detect_middles(
    events: Iterable[Mapping[str, Any]],
    *,
    bankroll: float = 1000.0,
    books: Any = None,
) -> List[Dict[str, Any]]:
    bankroll = float(bankroll)
    if not isfinite(bankroll) or bankroll <= 0:
        return []
    allowed = _norm_books(books)
    grouped = _best_quotes(events, allowed)
    found: List[Dict[str, Any]] = []
    for (event_key, family, selection), quotes in grouped.items():
        if family == "total":
            overs = [q for q in quotes if q["side"] == "over"]
            unders = [q for q in quotes if q["side"] == "under"]
            for over in overs:
                for under in unders:
                    if over["book"] == under["book"]:
                        continue
                    gap = under["point"] - over["point"]
                    if gap <= 0:
                        continue
                    plan = _stake_plan(over, under, bankroll)
                    found.append(_row(
                        market_id=f"{event_key}|middle|{selection}|{over['point']}|{under['point']}|{over['book']}|{under['book']}",
                        kind="total_middle",
                        family=family,
                        gap=gap,
                        plan=plan,
                        event=over["event"],
                        market=f"{over['market']}/{under['market']}",
                        commence_time=over.get("commence_time") or under.get("commence_time"),
                        selection=selection,
                    ))
        else:
            for left in quotes:
                for right in quotes:
                    if left["book"] == right["book"] or left["side"] == right["side"]:
                        continue
                    if (left["side"], left["book"]) > (right["side"], right["book"]):
                        continue
                    gap = left["point"] + right["point"]
                    if gap <= 0:
                        continue
                    plan = _spread_plan(left, right, bankroll)
                    found.append(_row(
                        market_id=f"{event_key}|middle|{left['side']}:{left['point']}|{right['side']}:{right['point']}|{left['book']}|{right['book']}",
                        kind="spread_middle",
                        family=family,
                        gap=gap,
                        plan=plan,
                        event=left["event"],
                        market=f"{left['market']}/{right['market']}",
                        commence_time=left.get("commence_time") or right.get("commence_time"),
                        selection=selection,
                    ))
    found.sort(key=lambda row: (
        0 if row["kind"] == "free_middle" else 1,
        -(row.get("gap") or 0),
        -(row.get("margin_pct") or row.get("pnl_if_middle_hits") or 0),
    ))
    return found[:MAX_MIDDLES]
