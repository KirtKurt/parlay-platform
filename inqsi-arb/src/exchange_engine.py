"""Deterministic sportsbook back vs exchange lay calculations for Inqsi ARB.

This module is pure calculation. It never connects to an exchange or places a
wager. Lay commission applies to exchange net winnings in the lay-win branch.
Liquidity and user balance/limits are explicit constraints rather than assumed.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Optional

CENT = Decimal("0.01")


class ExchangeArbError(ValueError):
    pass


def _d(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception as exc:
        raise ExchangeArbError(f"invalid numeric value: {value!r}") from exc


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def back_lay_plan(
    *,
    back_odds: Any,
    back_stake: Any,
    lay_odds: Any,
    commission_rate: Any = 0,
    lay_liquidity: Optional[Any] = None,
    max_liability: Optional[Any] = None,
    fixed_costs: Any = 0,
) -> Dict[str, Any]:
    """Equalize P&L between a sportsbook back leg and exchange lay leg.

    With back stake S, back decimal B, lay odds L, exchange commission c:
      if backed outcome wins: S(B-1) - lay_stake(L-1)
      otherwise:             -S + lay_stake(1-c)

    Equalized lay stake = S*B / (L-c).
    """
    b = _d(back_odds)
    s = _d(back_stake)
    l = _d(lay_odds)
    c = _d(commission_rate)
    costs = _d(fixed_costs)
    if b <= 1 or l <= 1:
        raise ExchangeArbError("back_odds and lay_odds must be > 1")
    if s <= 0:
        raise ExchangeArbError("back_stake must be positive")
    if c < 0 or c >= 1:
        raise ExchangeArbError("commission_rate must be in [0, 1)")
    if costs < 0:
        raise ExchangeArbError("fixed_costs cannot be negative")
    denominator = l - c
    if denominator <= 0:
        raise ExchangeArbError("invalid lay odds/commission combination")

    raw_lay_stake = s * b / denominator
    lay_stake = _money(raw_lay_stake)
    liability = _money(lay_stake * (l - Decimal("1")))
    available_liquidity = None if lay_liquidity is None else _d(lay_liquidity)
    liability_cap = None if max_liability is None else _d(max_liability)
    if available_liquidity is not None and available_liquidity < 0:
        raise ExchangeArbError("lay_liquidity cannot be negative")
    if liability_cap is not None and liability_cap < 0:
        raise ExchangeArbError("max_liability cannot be negative")

    liquidity_ok = available_liquidity is None or lay_stake <= available_liquidity
    liability_ok = liability_cap is None or liability <= liability_cap

    back_wins = _money(s * (b - Decimal("1")) - liability - costs)
    back_loses = _money(-s + lay_stake * (Decimal("1") - c) - costs)
    minimum_profit = min(back_wins, back_loses)
    total_capital = _money(s + liability)
    roi = (minimum_profit / total_capital * Decimal("100")) if total_capital > 0 else Decimal("0")
    mathematical_arb = minimum_profit > 0
    feasible = liquidity_ok and liability_ok

    return {
        "ok": True,
        "places_bets": False,
        "back_odds": float(b),
        "back_stake": float(_money(s)),
        "lay_odds": float(l),
        "lay_stake": float(lay_stake),
        "lay_liability": float(liability),
        "commission_rate": float(c),
        "fixed_costs": float(_money(costs)),
        "pnl_if_back_wins": float(back_wins),
        "pnl_if_back_loses": float(back_loses),
        "minimum_profit": float(minimum_profit),
        "capital_required": float(total_capital),
        "minimum_roi_pct_on_required_capital": float(roi.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)),
        "mathematical_arb": bool(mathematical_arb),
        "feasible": bool(feasible),
        "strict_candidate": bool(mathematical_arb and feasible),
        "constraints": {
            "lay_liquidity": None if available_liquidity is None else float(available_liquidity),
            "liquidity_known": available_liquidity is not None,
            "liquidity_ok": bool(liquidity_ok),
            "max_liability": None if liability_cap is None else float(liability_cap),
            "liability_cap_known": liability_cap is not None,
            "liability_ok": bool(liability_ok),
        },
        "policy": "Calculation only. A strict published arb still requires current prices, compatible reviewed settlement rules, sufficient exchange depth, and all other validation gates.",
    }
