import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import settlement_matrix


def test_two_way_strict_arb_all_states_positive():
    result = settlement_matrix.evaluate_states(
        states=["A_WINS", "B_WINS"],
        legs=[
            {"leg_id": "a", "stake": 50, "payout_multiplier_by_state": {"A_WINS": 2.2, "B_WINS": 0}},
            {"leg_id": "b", "stake": 50, "payout_multiplier_by_state": {"A_WINS": 0, "B_WINS": 2.2}},
        ],
    )
    assert result["strict_arbitrage"] is True
    assert result["minimum_net_pnl"] == 10.0


def test_oversized_payout_fails_as_settlement_proof_error():
    try:
        settlement_matrix.evaluate_states(
            states=["A", "B"],
            legs=[
                {"stake": 500, "payout_multiplier_by_state": {"A": "1e24", "B": 0}},
                {"stake": 500, "payout_multiplier_by_state": {"A": 0, "B": "1e24"}},
            ],
        )
    except settlement_matrix.SettlementProofError as exc:
        assert "supported precision" in str(exc)
    else:
        raise AssertionError("oversized monetary proof must fail closed")


def test_refund_branch_zero_profit_is_not_strict():
    result = settlement_matrix.evaluate_states(
        states=["A_WINS", "B_WINS", "BOTH_VOID"],
        legs=[
            {"stake": 50, "payout_multiplier_by_state": {"A_WINS": 2.2, "B_WINS": 0, "BOTH_VOID": 1}},
            {"stake": 50, "payout_multiplier_by_state": {"A_WINS": 0, "B_WINS": 2.2, "BOTH_VOID": 1}},
        ],
    )
    assert result["strict_arbitrage"] is False
    assert min(row["net_pnl"] for row in result["states"]) == 0.0


def test_one_sided_void_negative_branch_rejects_strict_arb():
    result = settlement_matrix.evaluate_states(
        states=["A_WINS", "B_WINS", "A_VOID_B_LOSES"],
        legs=[
            {"stake": 50, "payout_multiplier_by_state": {"A_WINS": 2.2, "B_WINS": 0, "A_VOID_B_LOSES": 1}},
            {"stake": 50, "payout_multiplier_by_state": {"A_WINS": 0, "B_WINS": 2.2, "A_VOID_B_LOSES": 0}},
        ],
    )
    assert result["strict_arbitrage"] is False
    assert result["minimum_net_pnl"] == -50.0


def test_missing_state_fails_closed():
    try:
        settlement_matrix.evaluate_states(
            states=["A", "B", "DRAW"],
            legs=[
                {"stake": 50, "payout_multiplier_by_state": {"A": 2.1, "B": 0}},
                {"stake": 50, "payout_multiplier_by_state": {"A": 0, "B": 2.1, "DRAW": 1}},
            ],
        )
    except settlement_matrix.SettlementProofError as exc:
        assert "coverage mismatch" in str(exc)
    else:
        raise AssertionError("incomplete states must fail closed")


def test_fixed_cost_can_erase_small_arb():
    result = settlement_matrix.evaluate_states(
        states=["A", "B"],
        legs=[
            {"stake": 50, "payout_multiplier_by_state": {"A": 2.02, "B": 0}},
            {"stake": 50, "payout_multiplier_by_state": {"A": 0, "B": 2.02}},
        ],
        fixed_costs=2,
    )
    assert result["strict_arbitrage"] is False
    assert result["minimum_net_pnl"] == -1.0


def test_quarter_line_helper_splits_stake_exactly():
    legs = settlement_matrix.split_quarter_line_leg(
        leg_id="q", book="book", stake=100,
        lower_state_multipliers={"W": 2, "P": 1},
        upper_state_multipliers={"W": 2, "P": 0},
    )
    assert len(legs) == 2
    assert sum(float(x["stake"]) for x in legs) == 100.0


def test_line_proof_rejects_same_side_total_and_spread_legs():
    cases = [
        ("totals", [
            {"outcome": "Over A", "point": 8.5, "stake": 50, "net_decimal": 2.2},
            {"outcome": "Over B", "point": 8.5, "stake": 50, "net_decimal": 2.2},
        ]),
        ("spreads", [
            {"outcome": "A +1.5", "point": 1.5, "stake": 50, "net_decimal": 2.2},
            {"outcome": "A -1.5", "point": -1.5, "stake": 50, "net_decimal": 2.2},
        ]),
    ]
    for market, legs in cases:
        try:
            settlement_matrix.prove_quoted_market(market=market, legs=legs)
        except settlement_matrix.SettlementProofError as exc:
            assert "opposing" in str(exc)
        else:
            raise AssertionError(f"{market} same-side legs must fail closed")


def test_line_proof_rejects_points_outside_finite_runtime_range():
    try:
        settlement_matrix.prove_quoted_market(market="totals", legs=[
            {"outcome": "Over", "point": "1e10000", "stake": 50, "net_decimal": 2.2},
            {"outcome": "Under", "point": "1e10000", "stake": 50, "net_decimal": 2.2},
        ])
    except settlement_matrix.SettlementProofError as exc:
        assert "finite runtime number" in str(exc)
    else:
        raise AssertionError("overflowing line points must fail closed")
