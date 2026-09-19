"""Cross-line middle detection for Inqsi ARB.

A middle is a line gap, not a same-contract surebet. This module never places
bets. Risk middles are never labelled arb=true. Free middles (implied sum < 1
across the gapped lines) are mathematical locks with extra upside if the gap
hits; they still require reviewed settlement compatibility to verify, which
this detector does not grant.
"""
from __future__ import annotations

import re
from math import floor, isfinite
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from arb_engine import _net_decimal, _quote_decimal
from stake_rounding import StakeRoundingError, optimize_rounding_neighborhood

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
    event_id = str(item.get("event_id") or "").strip()
    if event_id:
        return event_id
    item_id = str(item.get("id") or "").strip()
    if item_id:
        return item_id
    event = str(item.get("event") or "").strip()
    commence_time = str(item.get("commence_time") or "").strip()
    return f"{event}|{commence_time}" if event and commence_time else ""


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


def _contract(market: str) -> str:
    """Keep statistic/period identity while folding base/alternate variants."""
    parts = [part for part in str(market or "").strip().lower().split("_") if part != "alternate"]
    return "_".join(parts)


def _scoring_increment(contract: str, left: Mapping[str, Any], right: Mapping[str, Any]) -> Optional[float]:
    explicit = []
    for quote in (left, right):
        raw = quote.get("scoring_increment")
        if raw is None or raw == "":
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        if not isfinite(value) or value <= 0:
            return None
        explicit.append(value)
    if len(explicit) == 2 and abs(explicit[0] - explicit[1]) > 1e-9:
        return None
    if explicit:
        return explicit[0]
    return 0.01 if "fantasy" in contract else 1.0


def _middle_result_exists(lower: float, upper: float, increment: Optional[float]) -> bool:
    """Return whether an attainable score on the contract grid wins both legs."""
    if increment is None or increment <= 0 or upper <= lower:
        return False
    next_result = (floor(lower / increment + 1e-10) + 1) * increment
    return next_result < upper - 1e-9


def _participant_contract(contract: str) -> bool:
    return contract.startswith(("player_", "batter_", "pitcher_", "team_"))


def _best_quotes(events: Iterable[Mapping[str, Any]], allowed: Optional[set[str]]) -> Dict[Tuple[str, str, str, str], List[Dict[str, Any]]]:
    grouped: Dict[Tuple[str, str, str, str], Dict[Tuple[str, str, float], Dict[str, Any]]] = {}
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
            contract = _contract(market)
            if _participant_contract(contract) and not selection:
                continue
            bucket = grouped.setdefault((event_key, family, contract, selection), {})
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
                "min_stake": raw.get("min_stake"),
                "stake_increment": raw.get("stake_increment"),
                "scoring_increment": raw.get("scoring_increment", item.get("scoring_increment")),
            }
            current = bucket.get(key)
            if current is None or net > current["net_decimal"]:
                bucket[key] = candidate
    return {key: list(rows.values()) for key, rows in grouped.items()}


def _stake_plan(first: Mapping[str, Any], second: Mapping[str, Any], bankroll: float) -> Dict[str, Any]:
    quotes = (first, second)
    implied = sum(1.0 / quote["net_decimal"] for quote in quotes)
    if implied <= 0:
        return {"implied_sum": implied, "feasible": False, "legs": []}
    targets = [bankroll * (1.0 / quote["net_decimal"]) / implied for quote in quotes]
    cap_scales = [1.0]
    for target, quote in zip(targets, quotes):
        try:
            cap = float(quote["limit"]) if quote.get("limit") not in (None, "") else None
        except (TypeError, ValueError):
            cap = None
        if cap is not None and isfinite(cap) and cap >= 0:
            cap_scales.append(cap / target)
    scale = min(cap_scales)
    rounding_legs = []
    for index, (target, quote) in enumerate(zip(targets, quotes)):
        try:
            cap = float(quote["limit"]) if quote.get("limit") not in (None, "") else None
        except (TypeError, ValueError):
            cap = None
        rounding_legs.append({
            "outcome": f"leg_{index}",
            "book": quote["book"],
            "stake": target * scale,
            "net_decimal": quote["net_decimal"],
            "constraint_cap": cap,
            "min_stake": quote.get("min_stake") or 0,
            "stake_increment": quote.get("stake_increment") or 0.01,
        })
    try:
        rounded = optimize_rounding_neighborhood(rounding_legs, bankroll=bankroll)
    except StakeRoundingError:
        rounded = {"feasible": False, "reason": "ROUNDING_ERROR", "legs": []}
    if not rounded.get("feasible"):
        return {
            "implied_sum": implied,
            "feasible": False,
            "reason": rounded.get("reason") or "ROUNDING_INFEASIBLE",
            "legs": [],
        }
    rounded_by_key = {leg["outcome"]: leg for leg in rounded.get("legs") or []}
    legs = []
    for index, quote in enumerate(quotes):
        planned = rounded_by_key[f"leg_{index}"]
        stake = float(planned["stake"])
        payout = float(planned["payout_if_wins"])
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
            "min_stake": quote.get("min_stake"),
            "stake_increment": quote.get("stake_increment") or 0.01,
        })
    total = float(rounded["allocated_stake"])
    first_only = round(legs[0]["payout_if_wins"] - total, 2)
    second_only = round(legs[1]["payout_if_wins"] - total, 2)
    both = round(legs[0]["payout_if_wins"] + legs[1]["payout_if_wins"] - total, 2)
    return {
        "implied_sum": implied,
        "feasible": True,
        "strict_arbitrage_after_rounding": rounded.get("strict_arbitrage_after_rounding", False),
        "allocated_stake": total,
        "pnl_if_first_only": first_only,
        "pnl_if_second_only": second_only,
        "pnl_if_middle_hits": both,
        "minimum_miss_pnl": min(first_only, second_only),
        "legs": legs,
    }


def _spread_plan(left: Mapping[str, Any], right: Mapping[str, Any], bankroll: float) -> Dict[str, Any]:
    return _stake_plan(left, right, bankroll)


def _row(*, market_id: str, kind: str, family: str, gap: float, plan: Mapping[str, Any],
         event: str, market: str, commence_time: Any, selection: str) -> Dict[str, Any]:
    implied = float(plan.get("implied_sum") or 0.0)
    free = (bool(plan.get("feasible")) and implied > 0 and implied < 1.0
            and float(plan.get("minimum_miss_pnl") or 0.0) > 0)
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
    for (event_key, family, contract, selection), quotes in grouped.items():
        if family == "total":
            overs = [q for q in quotes if q["side"] == "over"]
            unders = [q for q in quotes if q["side"] == "under"]
            for over in overs:
                for under in unders:
                    if over["book"] == under["book"]:
                        continue
                    gap = under["point"] - over["point"]
                    increment = _scoring_increment(contract, over, under)
                    if gap <= 0 or not _middle_result_exists(over["point"], under["point"], increment):
                        continue
                    plan = _stake_plan(over, under, bankroll)
                    if not plan.get("feasible"):
                        continue
                    found.append(_row(
                        market_id=f"{event_key}|middle|{contract}|{selection}|{over['point']}|{under['point']}|{over['book']}|{under['book']}",
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
                    increment = _scoring_increment(contract, left, right)
                    if gap <= 0 or not _middle_result_exists(-left["point"], right["point"], increment):
                        continue
                    plan = _spread_plan(left, right, bankroll)
                    if not plan.get("feasible"):
                        continue
                    found.append(_row(
                        market_id=f"{event_key}|middle|{contract}|{left['side']}:{left['point']}|{right['side']}:{right['point']}|{left['book']}|{right['book']}",
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
