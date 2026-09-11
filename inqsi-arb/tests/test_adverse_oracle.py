"""Adverse-case verification using an oracle independent of production helpers.

The oracle below deliberately recomputes cash flows directly from declared state
multipliers instead of calling settlement_matrix internals. This guards against
false strict-arbitrage classifications caused by rounding/refund/void mistakes.
"""
from decimal import Decimal, ROUND_HALF_UP
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from settlement_matrix import SettlementProofError, evaluate_states

CENT = Decimal("0.01")


def oracle(states, legs, fixed_costs=0):
    total = sum(Decimal(str(leg["stake"])) for leg in legs)
    costs = Decimal(str(fixed_costs))
    pnl = {}
    for state in states:
        payout = Decimal("0")
        for leg in legs:
            payout += Decimal(str(leg["stake"])) * Decimal(str(leg["payout_multiplier_by_state"][state]))
        pnl[state] = (payout - total - costs).quantize(CENT, rounding=ROUND_HALF_UP)
    return pnl


def test_randomized_state_pnl_matches_independent_oracle():
    rng = random.Random(20260911)
    for _ in range(250):
        states = [f"S{i}" for i in range(rng.randint(2, 5))]
        legs = []
        for leg_index in range(rng.randint(2, 6)):
            stake = round(rng.uniform(1, 500), 2)
            multipliers = {state: round(rng.uniform(0, 4), 4) for state in states}
            legs.append({"leg_id": str(leg_index), "stake": stake, "payout_multiplier_by_state": multipliers})
        costs = round(rng.uniform(0, 10), 2)
        expected = oracle(states, legs, costs)
        result = evaluate_states(states=states, legs=legs, fixed_costs=costs)
        actual = {row["state"]: Decimal(str(row["net_pnl"])).quantize(CENT) for row in result["states"]}
        assert actual == expected
        assert result["strict_arbitrage"] is (min(expected.values()) > 0)


def test_tiny_pre_rounding_edge_does_not_survive_cent_rounding():
    # Each branch has a sub-cent theoretical gain. Cent rounding produces 0.00,
    # therefore this is not a strict positive-profit arbitrage.
    legs = [
        {"stake": "50.00", "payout_multiplier_by_state": {"A": "2.00008", "B": "0"}},
        {"stake": "50.00", "payout_multiplier_by_state": {"A": "0", "B": "2.00008"}},
    ]
    result = evaluate_states(states=["A", "B"], legs=legs)
    assert result["minimum_net_pnl"] == 0.0
    assert result["strict_arbitrage"] is False


def test_integer_push_branch_rejects_strict_positive_claim():
    legs = [
        {"stake": 50, "payout_multiplier_by_state": {"OVER": 2.2, "UNDER": 0, "PUSH": 1}},
        {"stake": 50, "payout_multiplier_by_state": {"OVER": 0, "UNDER": 2.2, "PUSH": 1}},
    ]
    result = evaluate_states(states=["OVER", "UNDER", "PUSH"], legs=legs)
    assert result["strict_arbitrage"] is False
    assert {row["state"]: row["net_pnl"] for row in result["states"]}["PUSH"] == 0.0


def test_dead_heat_branch_is_evaluated_not_ignored():
    # One leg receives half of normal gross return in a dead heat; the other loses.
    legs = [
        {"stake": 50, "payout_multiplier_by_state": {"A_ONLY": 2.2, "B_ONLY": 0, "DEAD_HEAT": 1.1}},
        {"stake": 50, "payout_multiplier_by_state": {"A_ONLY": 0, "B_ONLY": 2.2, "DEAD_HEAT": 0}},
    ]
    result = evaluate_states(states=["A_ONLY", "B_ONLY", "DEAD_HEAT"], legs=legs)
    assert result["strict_arbitrage"] is False
    assert {row["state"]: row["net_pnl"] for row in result["states"]}["DEAD_HEAT"] == -45.0


def test_partial_void_branch_is_evaluated_not_ignored():
    legs = [
        {"stake": 60, "payout_multiplier_by_state": {"A": 2.0, "B": 0, "LEG_A_VOID": 1}},
        {"stake": 40, "payout_multiplier_by_state": {"A": 0, "B": 2.5, "LEG_A_VOID": 0}},
    ]
    result = evaluate_states(states=["A", "B", "LEG_A_VOID"], legs=legs)
    assert result["strict_arbitrage"] is False
    assert result["minimum_net_pnl"] < 0


def test_duplicate_or_underspecified_state_universe_fails_closed():
    for states in (["A", "A"], ["A"], []):
        try:
            evaluate_states(
                states=states,
                legs=[
                    {"stake": 50, "payout_multiplier_by_state": {"A": 2}},
                    {"stake": 50, "payout_multiplier_by_state": {"A": 0}},
                ],
            )
        except SettlementProofError:
            pass
        else:
            raise AssertionError("invalid state universe must fail closed")
