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

import re
from decimal import Decimal, DecimalException, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
from math import isfinite
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
    try:
        return value.quantize(CENT, rounding=ROUND_HALF_UP)
    except DecimalException as exc:
        raise SettlementProofError("monetary value exceeds supported precision") from exc


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
    except (TypeError, ValueError, OverflowError) as exc:
        raise SettlementProofError("line point is not a finite runtime number") from exc
    if not isfinite(value):
        raise SettlementProofError("line point is not a finite runtime number")
    return abs(value - round(value)) < 1e-9


def _is_quarter_line(point: Any) -> bool:
    try:
        value = _d(point)
    except SettlementProofError:
        return False
    floor = value.to_integral_value(rounding=ROUND_FLOOR)
    return value - floor in {Decimal("0.25"), Decimal("0.75")}


def _split_line(point: Any) -> List[Decimal]:
    value = _d(point)
    floor = value.to_integral_value(rounding=ROUND_FLOOR)
    fraction = value - floor
    if fraction == Decimal("0.25"):
        return [floor, floor + Decimal("0.5")]
    if fraction == Decimal("0.75"):
        return [floor + Decimal("0.5"), floor + Decimal("1")]
    return [value]


def _settled_multiplier(adjusted: Decimal, decimal: Decimal) -> Decimal:
    if adjusted > 0:
        return decimal
    if adjusted == 0:
        return Decimal("1")
    return Decimal("0")


def _prove_quarter_lines(*, market: str, legs: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Expand Asian quarter lines into equal adjacent-line subpositions."""
    if len(legs) != 2:
        return {
            "ok": False, "states_complete": False, "strict_arbitrage": False,
            "includes_push": True, "reason": "QUARTER_LINE_REQUIRES_TWO_WAY_MARKET",
            "minimum_net_pnl": None, "state_count": 0, "states": [],
        }
    key = str(market or "").lower()
    total_market = "total" in key
    spread_market = "spread" in key or "handicap" in key
    if not total_market and not spread_market:
        return {
            "ok": False, "states_complete": False, "strict_arbitrage": False,
            "includes_push": True, "reason": "QUARTER_LINE_MARKET_UNSUPPORTED",
            "minimum_net_pnl": None, "state_count": 0, "states": [],
        }

    first_outcome = str(legs[0].get("outcome") or "")
    expanded: List[Dict[str, Any]] = []
    thresholds: List[Decimal] = []
    for leg in legs:
        point = leg.get("point")
        if point is None:
            return {
                "ok": False, "states_complete": False, "strict_arbitrage": False,
                "includes_push": True, "reason": "QUARTER_LINE_POINT_MISSING",
                "minimum_net_pnl": None, "state_count": 0, "states": [],
            }
        lines = _split_line(point)
        outcome = str(leg.get("outcome") or "")
        decimal = _d(leg.get("net_decimal") if leg.get("net_decimal") is not None else leg.get("decimal"))
        if decimal <= 1:
            raise SettlementProofError("decimal odds must be greater than 1")
        if total_market:
            lower = outcome.lower()
            if "over" not in lower and "under" not in lower:
                return {
                    "ok": False, "states_complete": False, "strict_arbitrage": False,
                    "includes_push": True, "reason": "QUARTER_TOTAL_SIDE_UNKNOWN",
                    "minimum_net_pnl": None, "state_count": 0, "states": [],
                }
            thresholds.extend(lines)
        else:
            same_side = outcome == first_outcome
            thresholds.extend([-line if same_side else line for line in lines])
        expanded.append({**dict(leg), "lines": lines, "decimal_value": decimal})

    result_values = set()
    for threshold in thresholds:
        floor = int(threshold.to_integral_value(rounding=ROUND_FLOOR))
        result_values.update({floor - 1, floor, floor + 1, floor + 2})
    if total_market:
        result_values = {value for value in result_values if value >= 0}
    ordered_results = sorted(result_values)
    states = [f"RESULT_{value}" for value in ordered_results]
    matrix_legs = []
    for leg in expanded:
        mapping: Dict[str, Any] = {}
        outcome = str(leg.get("outcome") or "")
        for value, state in zip(ordered_results, states):
            payouts = []
            for line in leg["lines"]:
                if total_market:
                    adjusted = Decimal(value) - line
                    if "under" in outcome.lower():
                        adjusted = -adjusted
                else:
                    margin = Decimal(value) if outcome == first_outcome else Decimal(-value)
                    adjusted = margin + line
                payouts.append(_settled_multiplier(adjusted, leg["decimal_value"]))
            mapping[state] = sum(payouts, Decimal("0")) / Decimal(len(payouts))
        matrix_legs.append({
            "leg_id": str(leg.get("leg_id") or outcome),
            "book": str(leg.get("book") or ""),
            "stake": leg.get("stake"),
            "payout_multiplier_by_state": mapping,
        })
    result = evaluate_states(states=states, legs=matrix_legs)
    result.update({
        "includes_push": True,
        "quarter_line_expanded": True,
        "market": str(market or ""),
    })
    return result


def _line_market(market: str) -> bool:
    key = str(market or "").lower()
    return "spread" in key or "handicap" in key or "total" in key


def _spread_selection(outcome: str) -> str:
    return re.sub(r"\s*[+-]?\d+(?:\.\d+)?\s*$", "", str(outcome or "").lower()).strip(" :+-\t")


def prove_quoted_market(*, market: str, legs: Sequence[Mapping[str, Any]],
                        push_policy: Optional[str] = None,
                        shortened_game_policy: Optional[str] = None) -> Optional[Dict[str, Any]]:
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
    if _line_market(market):
        if len(points) != len(prepared):
            raise SettlementProofError("every line-market leg requires a point")
        try:
            finite_points = all(_d(point).is_finite() for point in points)
        except SettlementProofError:
            finite_points = False
        if not finite_points:
            raise SettlementProofError("line-market points must be finite numbers")
        if len(prepared) != 2:
            raise SettlementProofError("line-market proof requires two complementary legs")
        key = str(market or "").lower()
        first, second = (_d(point) for point in points)
        if "total" in key:
            directions = [
                "over" if re.search(r"\bover\b", outcome.lower()) else
                "under" if re.search(r"\bunder\b", outcome.lower()) else ""
                for outcome in outcomes
            ]
            if set(directions) != {"over", "under"}:
                raise SettlementProofError("total legs must be opposing over and under selections")
            if first != second:
                raise SettlementProofError("total legs must use the same line")
        if "spread" in key or "handicap" in key:
            selections = [_spread_selection(outcome) for outcome in outcomes]
            if not all(selections) or selections[0] == selections[1]:
                raise SettlementProofError("spread legs must be opposing selections")
            if first + second != 0:
                raise SettlementProofError("spread legs must use complementary lines")
    policy = str(push_policy or "").strip().lower()
    if policy == "market_specific":
        raise SettlementProofError(
            "market-specific push policy requires an explicit settlement-state model"
        )
    shortened_policy = str(shortened_game_policy or "").strip().lower()
    if points and any(_is_quarter_line(point) for point in points):
        if shortened_policy:
            raise SettlementProofError(
                "quarter-line proof requires an explicit shortened-game state model"
            )
        return _prove_quarter_lines(market=market, legs=prepared)
    line_can_push = bool(points) and all(_is_integer_line(point) for point in points) and _line_market(market)
    declared_refund_state = (
        policy == "refund"
        or (len(outcomes) == 2 and policy in {"tie_push", "tie_refund", "tied_period_void"})
    )
    # A generic `push` policy describes settlement if the line lands exactly;
    # it does not make a half-point line or overtime-decided winner push.
    include_push = declared_refund_state or line_can_push
    states = list(outcomes)
    if include_push:
        states.append("PUSH")
    if shortened_policy:
        states.append("VOID")
    matrix_legs = []
    for leg in prepared:
        outcome = str(leg.get("outcome") or "")
        decimal = _d(leg.get("net_decimal") if leg.get("net_decimal") is not None else leg.get("decimal"))
        if decimal <= 1:
            raise SettlementProofError("decimal odds must be greater than 1")
        mapping: Dict[str, Any] = {}
        for state in states:
            if state in {"PUSH", "VOID"}:
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
    result["push_policy"] = str(push_policy or "") or None
    result["shortened_game_policy"] = str(shortened_game_policy or "") or None
    result["market"] = str(market or "")
    return result
