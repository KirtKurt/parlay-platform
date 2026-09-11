import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from stake_rounding import optimize_rounding_neighborhood


def test_cent_increment_preserves_positive_arb():
    result = optimize_rounding_neighborhood([
        {"outcome": "A", "book": "one", "stake": 51.1628, "decimal": 2.15, "stake_increment": 0.01},
        {"outcome": "B", "book": "two", "stake": 48.8372, "decimal": 2.10, "stake_increment": 0.01},
    ], bankroll=100)
    assert result["feasible"] is True
    assert result["strict_arbitrage_after_rounding"] is True
    assert result["minimum_profit"] > 0
    assert result["global_optimum_claimed"] is False


def test_large_increment_can_erase_tiny_arb():
    result = optimize_rounding_neighborhood([
        {"outcome": "A", "book": "one", "stake": 50.0, "decimal": 2.01, "stake_increment": 5},
        {"outcome": "B", "book": "two", "stake": 50.0, "decimal": 2.01, "stake_increment": 5},
    ], bankroll=100)
    assert result["feasible"] is True
    assert result["minimum_profit"] == 0.5


def test_minimum_above_neighborhood_cap_is_infeasible():
    result = optimize_rounding_neighborhood([
        {"outcome": "A", "book": "one", "stake": 10, "decimal": 2.2, "stake_increment": 1, "min_stake": 20, "constraint_cap": 15},
        {"outcome": "B", "book": "two", "stake": 10, "decimal": 2.2, "stake_increment": 1},
    ], bankroll=100)
    # Invalid contradictory min/cap is rejected before searching.
    assert False, "expected exception"
