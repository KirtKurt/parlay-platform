import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from exchange_engine import ExchangeArbError, back_lay_plan


def test_equalized_back_lay_profit_with_commission():
    result = back_lay_plan(back_odds=2.2, back_stake=100, lay_odds=2.0, commission_rate=0.02)
    assert result["places_bets"] is False
    assert abs(result["pnl_if_back_wins"] - result["pnl_if_back_loses"]) <= 0.03
    assert result["minimum_profit"] > 0
    assert result["mathematical_arb"] is True
    assert result["strict_candidate"] is True


def test_liquidity_shortfall_makes_candidate_infeasible():
    result = back_lay_plan(
        back_odds=2.2, back_stake=100, lay_odds=2.0, commission_rate=0.02,
        lay_liquidity=50,
    )
    assert result["mathematical_arb"] is True
    assert result["feasible"] is False
    assert result["constraints"]["liquidity_ok"] is False
    assert result["strict_candidate"] is False


def test_liability_cap_makes_candidate_infeasible():
    result = back_lay_plan(
        back_odds=2.2, back_stake=100, lay_odds=2.0, commission_rate=0.02,
        max_liability=50,
    )
    assert result["feasible"] is False
    assert result["constraints"]["liability_ok"] is False


def test_costs_can_erase_exchange_arb():
    result = back_lay_plan(
        back_odds=2.2, back_stake=100, lay_odds=2.0, commission_rate=0.02,
        fixed_costs=20,
    )
    assert result["mathematical_arb"] is False
    assert result["minimum_profit"] <= 0


def test_invalid_commission_fails():
    for commission in (-0.01, 1, 1.1):
        try:
            back_lay_plan(back_odds=2.2, back_stake=100, lay_odds=2.0, commission_rate=commission)
        except ExchangeArbError:
            pass
        else:
            raise AssertionError("invalid commission must fail")
