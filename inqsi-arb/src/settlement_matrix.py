"""Deterministic settlement-state proof for complex Inqsi ARB contracts.

This module evaluates explicit, exhaustive settlement states. It never infers
missing states and never uses simulation as proof. A strict arbitrage requires
positive net P&L in every declared state after known fees.

Each leg supplies a stake and a `payout_multiplier_by_state` mapping. The payout
multiplier is total cash returned per unit stake: loss=0, refund/push=1,
full win=decimal odds, half-win/half-push/etc. can be represented directly or by
splitting the position into sub-legs. Unknown states fail closed.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

CENT = Decimal("0.01")


class SettlementProofError(ValueError):
    pass


def _d(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception as exc:
        raise SettlementProofError(f"invalid numeric value: {value!r}") from exc


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def evaluate_states(
    *,
    states: Sequence[str],
    legs: Iterable[Mapping[str, Any]],
    fixed_costs: Any = 0,
    require_positive: bool = True,
) -> Dict[str, Any]:
    normalized_states = [str(s).strip() for s in states if str(s).strip()]
    if len(normalized_states) < 2 or len(set(normalized_states)) != len(normalized_states):
        raise SettlementProofError("states must contain at least two unique values")

    prepared: List[Dict[str, Any]] = []
    total_stake = Decimal("0")
    for index, raw in enumerate(legs or []):
        stake = _d(raw.get("stake"))
        if stake <= 0:
            raise SettlementProofError("each leg stake must be positive")
        mapping = raw.get("payout_multiplier_by_state")
        if not isinstance(mapping, Mapping):
            raise SettlementProofError("each leg requires payout_multiplier_by_state")
        missing = [state for state in normalized_states if state not in mapping]
        extras = [str(state) for state in mapping if str(state) not in set(normalized_states)]
        if missing or extras:
            raise SettlementProofError(f"leg {index} state coverage mismatch missing={missing} extra={extras}")
        multipliers: Dict[str, Decimal] = {}
        for state in normalized_states:
            multiplier = _d(mapping[state])
            if multiplier < 0:
                raise SettlementProofError("payout multipliers cannot be negative")
            multipliers[state] = multiplier
        total_stake += stake
        prepared.append({
            "leg_id": str(raw.get("leg_id") or index),
            "book": str(raw.get("book") or ""),
            "stake": stake,
            "multipliers": multipliers,
        })

    if len(prepared) < 2:
        raise SettlementProofError("at least two legs are required")
    costs = _d(fixed_costs)
    if costs < 0:
        raise SettlementProofError("fixed_costs cannot be negative")

    state_rows: List[Dict[str, Any]] = []
    minimum = None
    for state in normalized_states:
        payout = sum((leg["stake"] * leg["multipliers"][state] for leg in prepared), Decimal("0"))
        pnl = payout - total_stake - costs
        pnl_money = _money(pnl)
        payout_money = _money(payout)
        state_rows.append({
            "state": state,
            "payout": float(payout_money),
            "net_pnl": float(pnl_money),
        })
        minimum = pnl_money if minimum is None or pnl_money < minimum else minimum

    strict = minimum is not None and (minimum > 0 if require_positive else minimum >= 0)
    return {
        "ok": True,
        "proof_type": "exhaustive_declared_settlement_states",
        "states_complete": True,
        "state_count": len(normalized_states),
        "leg_count": len(prepared),
        "total_stake": float(_money(total_stake)),
        "fixed_costs": float(_money(costs)),
        "minimum_net_pnl": float(minimum) if minimum is not None else None,
        "strict_arbitrage": bool(strict),
        "require_positive": bool(require_positive),
        "states": state_rows,
    }


def split_quarter_line_leg(*, leg_id: str, book: str, stake: Any,
                           lower_state_multipliers: Mapping[str, Any],
                           upper_state_multipliers: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Represent an Asian quarter-line stake as two equal sub-legs.

    The caller supplies already-reviewed settlement multipliers for the adjacent
    half-lines. This helper does not infer sport rules or line semantics.
    """
    amount = _d(stake)
    if amount <= 0:
        raise SettlementProofError("stake must be positive")
    half = amount / Decimal("2")
    return [
        {
            "leg_id": f"{leg_id}:lower",
            "book": book,
            "stake": str(half),
            "payout_multiplier_by_state": dict(lower_state_multipliers),
        },
        {
            "leg_id": f"{leg_id}:upper",
            "book": book,
            "stake": str(half),
            "payout_multiplier_by_state": dict(upper_state_multipliers),
        },
    ]


def _is_integer_line(point: Any) -> bool:
    try:
        value = abs(float(point))
    except (TypeError, ValueError):
        return False
    return abs(value - round(value)) < 1e-9


def _line_market(market: str) -> bool:
    key = str(market or "").lower()
    return "spread" in key or "handicap" in key or "total" in key


def prove_quoted_market(*, market: str, legs: Sequence[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    """Prove the declared win/loss/push universe for a live 2-way or N-way quote.

    Spreads and totals with an integer line include PUSH as a refund state.
    Half-point lines and moneylines do not invent a push. A zero-profit push is
    not a strict surebet.
    """
    prepared = [dict(leg) for leg in legs or [] if leg.get("stake") and leg.get("outcome")]
    if len(prepared) < 2:
        return None
    outcomes = [str(leg.get("outcome") or "") for leg in prepared]
    if len(set(outcomes)) != len(outcomes):
        raise SettlementProofError("duplicate outcomes cannot prove settlement states")
    points = [leg.get("point") for leg in prepared if leg.get("point") is not None]
    include_push = bool(points) and all(_is_integer_line(point) for point in points) and _line_market(market)
    states = list(outcomes)
    if include_push:
        states.append("PUSH")
    matrix_legs = []
    for leg in prepared:
        outcome = str(leg.get("outcome") or "")
        decimal = _d(leg.get("net_decimal") if leg.get("net_decimal") is not None else leg.get("decimal"))
        if decimal <= 1:
            raise SettlementProofError("decimal odds must be greater than 1")
        mapping: Dict[str, Any] = {}
        for state in states:
            if state == "PUSH":
                mapping[state] = 1
            elif state == outcome:
                mapping[state] = decimal
            else:
                mapping[state] = 0
        matrix_legs.append({
            "leg_id": str(leg.get("leg_id") or outcome),
            "book": str(leg.get("book") or ""),
            "stake": leg.get("stake"),
            "payout_multiplier_by_state": mapping,
        })
    result = evaluate_states(states=states, legs=matrix_legs)
    result["includes_push"] = include_push
    result["market"] = str(market or "")
    return result
