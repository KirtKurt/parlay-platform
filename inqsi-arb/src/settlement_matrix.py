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
from typing import Any, Dict, Iterable, List, Mapping, Sequence

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
