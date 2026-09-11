"""Discrete stake rounding and verification for Inqsi ARB.

Sportsbooks often require stake increments and minimums. This module takes a
continuous candidate plan and searches the floor/ceil neighborhood for the
combination with the highest verified minimum P&L while respecting bankroll,
per-leg caps, minimums, and increments. It does not claim a global mixed-integer
optimum outside that neighborhood.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
from itertools import product
from typing import Any, Dict, Iterable, List, Mapping, Optional

CENT = Decimal("0.01")
MAX_ENUMERATED_LEGS = 12


class StakeRoundingError(ValueError):
    pass


def _d(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception as exc:
        raise StakeRoundingError(f"invalid numeric value: {value!r}") from exc


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _multiple_floor(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_FLOOR) * step


def _multiple_ceil(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_CEILING) * step


def optimize_rounding_neighborhood(
    legs: Iterable[Mapping[str, Any]],
    *,
    bankroll: Any,
) -> Dict[str, Any]:
    bankroll_d = _d(bankroll)
    if bankroll_d <= 0:
        raise StakeRoundingError("bankroll must be positive")

    prepared: List[Dict[str, Any]] = []
    for raw in legs or []:
        row = dict(raw)
        outcome = str(row.get("outcome") or "").strip()
        book = str(row.get("book") or "").strip()
        stake = _d(row.get("stake"))
        odds = _d(row.get("net_decimal") if row.get("net_decimal") is not None else row.get("decimal"))
        increment = _d(row.get("stake_increment") if row.get("stake_increment") is not None else "0.01")
        minimum = _d(row.get("min_stake") if row.get("min_stake") is not None else "0")
        cap_raw = row.get("constraint_cap") if row.get("constraint_cap") is not None else row.get("max_stake")
        cap: Optional[Decimal] = None if cap_raw is None else _d(cap_raw)
        if not outcome or not book or stake <= 0 or odds <= 1:
            raise StakeRoundingError("each leg requires outcome, book, stake>0, and decimal odds>1")
        if increment <= 0:
            raise StakeRoundingError("stake_increment must be positive")
        if minimum < 0 or (cap is not None and cap < 0):
            raise StakeRoundingError("stake bounds cannot be negative")
        if cap is not None and minimum > cap:
            raise StakeRoundingError("min_stake exceeds cap")

        floor_value = _multiple_floor(stake, increment)
        ceil_value = _multiple_ceil(stake, increment)
        candidates = {floor_value, ceil_value}
        if minimum > 0:
            candidates.add(_multiple_ceil(minimum, increment))
        valid = sorted({
            c for c in candidates
            if c >= minimum and c > 0 and (cap is None or c <= cap)
        })
        if not valid:
            return {
                "ok": True,
                "feasible": False,
                "reason": "NO_VALID_DISCRETE_STAKE_FOR_LEG",
                "outcome": outcome,
                "book": book,
                "places_bets": False,
            }
        prepared.append({
            **row,
            "outcome": outcome,
            "book": book,
            "odds": odds,
            "stake_increment": increment,
            "min_stake": minimum,
            "constraint_cap": cap,
            "candidates": valid,
        })

    if len(prepared) < 2:
        raise StakeRoundingError("at least two legs are required")
    if len(prepared) > MAX_ENUMERATED_LEGS:
        return {
            "ok": True,
            "feasible": False,
            "reason": "TOO_MANY_LEGS_FOR_LOCAL_ENUMERATION",
            "max_legs": MAX_ENUMERATED_LEGS,
            "places_bets": False,
        }

    best = None
    combinations_checked = 0
    for stakes in product(*(row["candidates"] for row in prepared)):
        combinations_checked += 1
        total = sum(stakes, Decimal("0"))
        if total > bankroll_d:
            continue
        profits = []
        payouts = []
        for row, stake in zip(prepared, stakes):
            payout = stake * row["odds"]
            payouts.append(payout)
            profits.append(payout - total)
        minimum_profit = min(profits)
        score = (minimum_profit, -total)
        if best is None or score > best["score"]:
            best = {
                "score": score,
                "stakes": stakes,
                "total": total,
                "profits": profits,
                "payouts": payouts,
            }

    if best is None:
        return {
            "ok": True,
            "feasible": False,
            "reason": "NO_COMBINATION_WITHIN_BANKROLL",
            "combinations_checked": combinations_checked,
            "places_bets": False,
        }

    rows: List[Dict[str, Any]] = []
    for row, stake, payout, profit in zip(prepared, best["stakes"], best["payouts"], best["profits"]):
        rows.append({
            "outcome": row["outcome"],
            "book": row["book"],
            "decimal": float(row["odds"]),
            "stake": float(_money(stake)),
            "stake_increment": float(row["stake_increment"]),
            "min_stake": float(row["min_stake"]),
            "constraint_cap": None if row["constraint_cap"] is None else float(row["constraint_cap"]),
            "payout_if_wins": float(_money(payout)),
            "profit_if_wins": float(_money(profit)),
        })

    min_profit = _money(min(best["profits"]))
    total = _money(best["total"])
    return {
        "ok": True,
        "feasible": True,
        "places_bets": False,
        "optimization_scope": "floor/ceil neighborhood of supplied continuous plan",
        "global_optimum_claimed": False,
        "combinations_checked": combinations_checked,
        "bankroll": float(_money(bankroll_d)),
        "allocated_stake": float(total),
        "unused_bankroll": float(_money(bankroll_d - best["total"])),
        "minimum_profit": float(min_profit),
        "strict_arbitrage_after_rounding": bool(min_profit > 0),
        "legs": rows,
    }
