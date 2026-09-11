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
