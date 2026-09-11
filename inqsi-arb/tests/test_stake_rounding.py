import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from stake_rounding import StakeRoundingError, optimize_rounding_neighborhood


def test_cent_increment_preserves_positive_arb():
    result = optimize_rounding_neighborhood([
        {"outcome": "A", "book": "one", "stake": 51.1628, "decimal": 2.15, "stake_increment": 0.01},
        {"outcome": "B", "book": "two", "stake": 48.8372, "decimal": 2.10, "stake_increment": 0.01},
    ], bankroll=100)
    assert result["feasible"] is True
    assert result["strict_arbitrage_after_rounding"] is True
    assert result["minimum_profit"] > 0
    assert result["global_optimum_claimed"] is False


def test_large_increment_still_recomputes_actual_profit():
    result = optimize_rounding_neighborhood([
        {"outcome": "A", "book": "one", "stake": 50.0, "decimal": 2.01, "stake_increment": 5},
        {"outcome": "B", "book": "two", "stake": 50.0, "decimal": 2.01, "stake_increment": 5},
    ], bankroll=100)
    assert result["feasible"] is True
    assert result["minimum_profit"] == 0.5
    assert result["strict_arbitrage_after_rounding"] is True


def test_minimum_above_cap_is_rejected():
    try:
        optimize_rounding_neighborhood([
            {"outcome": "A", "book": "one", "stake": 10, "decimal": 2.2, "stake_increment": 1, "min_stake": 20, "constraint_cap": 15},
            {"outcome": "B", "book": "two", "stake": 10, "decimal": 2.2, "stake_increment": 1},
        ], bankroll=100)
    except StakeRoundingError as exc:
        assert "min_stake exceeds cap" in str(exc)
    else:
        raise AssertionError("contradictory min/cap must fail")


def test_bankroll_can_make_all_discrete_combinations_infeasible():
    result = optimize_rounding_neighborhood([
        {"outcome": "A", "book": "one", "stake": 60, "decimal": 2.2, "stake_increment": 10, "min_stake": 60},
        {"outcome": "B", "book": "two", "stake": 60, "decimal": 2.2, "stake_increment": 10, "min_stake": 60},
    ], bankroll=100)
    assert result["feasible"] is False
    assert result["reason"] == "NO_COMBINATION_WITHIN_BANKROLL"
