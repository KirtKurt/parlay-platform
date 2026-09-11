"""Constraint analysis and stake optimization for hypothetical ARB plans.

Pure calculation only: this module never places transactions or communicates
with sportsbooks. It treats balances and limits as user-supplied constraints.
"""
from __future__ import annotations

from math import isfinite
from typing import Any, Dict, Iterable, List, Mapping, Optional


def _number(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _leg_cap(leg: Mapping[str, Any], profile: Mapping[str, Any]) -> Optional[float]:
    caps: List[float] = []
    for value in (profile.get("balance"), profile.get("max_stake"), leg.get("limit")):
        number = _number(value)
        if number is not None and number >= 0:
            caps.append(number)
    return min(caps) if caps else None


def apply_book_constraints(legs: Iterable[Mapping[str, Any]], profiles: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    feasible = True
    reasons: List[str] = []
    for raw in legs or []:
        leg = dict(raw)
        book = str(leg.get("book") or "").strip().lower()
        required = _number(leg.get("stake")) or 0.0
        profile = dict(profiles.get(book) or {})
        cap = _leg_cap(leg, profile)
        leg_ok = cap is None or required <= cap + 1e-9
        if not leg_ok:
            feasible = False
            reasons.append(f"{book}: required {required:.2f} exceeds available cap {cap:.2f}")
        leg["constraint_cap"] = cap
        leg["constraint_status"] = "OK" if leg_ok else "EXCEEDS_CAP"
        rows.append(leg)
    return {"feasible": feasible, "legs": rows, "reasons": reasons}


def optimize_equal_payout(
    legs: Iterable[Mapping[str, Any]],
    profiles: Mapping[str, Mapping[str, Any]],
    *,
    bankroll: float,
) -> Dict[str, Any]:
    """Maximize equalized guaranteed payout subject to balance/stake caps.

    For an arbitrage set with effective decimal odds d_i, equal payout requires
    stake_i = payout / d_i. The maximum safe payout is therefore bounded by the
    requested bankroll and by every per-book cap. If a cap binds, unused bankroll
    is deliberately left unallocated rather than distorting the guaranteed P&L.
    """
    bankroll_value = _number(bankroll)
    if bankroll_value is None or bankroll_value <= 0:
        raise ValueError("bankroll must be positive")

    prepared: List[Dict[str, Any]] = []
    seen_outcomes = set()
    for raw in legs or []:
        leg = dict(raw)
        outcome = str(leg.get("outcome") or "").strip()
        book = str(leg.get("book") or "").strip().lower()
        decimal = _number(leg.get("net_decimal"))
        if decimal is None:
            decimal = _number(leg.get("decimal"))
        if not outcome or not book or decimal is None or decimal <= 1:
            raise ValueError("each leg requires outcome, book, and decimal/net_decimal > 1")
        if outcome in seen_outcomes:
            raise ValueError("duplicate outcome in optimization set")
        seen_outcomes.add(outcome)
        profile = dict(profiles.get(book) or {})
        cap = _leg_cap(leg, profile)
        prepared.append({**leg, "outcome": outcome, "book": book, "effective_decimal": decimal, "constraint_cap": cap})

    if len(prepared) < 2:
        raise ValueError("at least two distinct outcomes are required")

    implied_sum = sum(1.0 / row["effective_decimal"] for row in prepared)
    if implied_sum <= 0:
        raise ValueError("invalid implied probability sum")

    bankroll_payout_cap = bankroll_value / implied_sum
    cap_payouts = [row["constraint_cap"] * row["effective_decimal"] for row in prepared if row["constraint_cap"] is not None]
    target_payout = min([bankroll_payout_cap, *cap_payouts]) if cap_payouts else bankroll_payout_cap
    target_payout = max(0.0, target_payout)

    stakes_unrounded = [target_payout / row["effective_decimal"] for row in prepared]
    stakes = [round(value, 2) for value in stakes_unrounded]
    total_stake = round(sum(stakes), 2)

    # Rounding can put a leg a cent above a hard cap. Trim only the offending leg;
    # never increase another leg because that could invalidate the equal-payout floor.
    for index, row in enumerate(prepared):
        cap = row["constraint_cap"]
        if cap is not None and stakes[index] > cap:
            stakes[index] = max(0.0, round(cap, 2))
    total_stake = round(sum(stakes), 2)

    rows: List[Dict[str, Any]] = []
    outcome_profits: Dict[str, float] = {}
    for row, stake in zip(prepared, stakes):
        payout = round(stake * row["effective_decimal"], 2)
        profit = round(payout - total_stake, 2)
        outcome_profits[row["outcome"]] = profit
        rows.append({
            **row,
            "stake": stake,
            "payout_if_wins": payout,
            "profit_if_wins": profit,
            "constraint_status": "OK" if row["constraint_cap"] is None or stake <= row["constraint_cap"] + 1e-9 else "EXCEEDS_CAP",
        })

    guaranteed_profit = min(outcome_profits.values()) if outcome_profits else None
    return {
        "feasible": bool(rows) and all(row["constraint_status"] == "OK" for row in rows),
        "is_mathematical_arb": implied_sum < 1.0,
        "bankroll": round(bankroll_value, 2),
        "allocated_stake": total_stake,
        "unused_bankroll": round(max(0.0, bankroll_value - total_stake), 2),
        "sum_implied": round(implied_sum, 8),
        "target_equal_payout": round(target_payout, 2),
        "guaranteed_profit": guaranteed_profit,
        "guaranteed_roi_pct": round((guaranteed_profit / total_stake) * 100.0, 4) if guaranteed_profit is not None and total_stake > 0 else None,
        "legs": rows,
        "constraint_bound": bool(cap_payouts and target_payout < bankroll_payout_cap - 1e-9),
    }
