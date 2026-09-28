import pytest
from inqsi_intelligence.favorite_bias import (
 compare_binary, slate_audit, upset_count_distribution,
 assert_fundamentals_market_free, active_vs_diagnostic)

def test_market_flip_is_observable_not_forced():
    r=compare_binary(event_id="g1",p_fundamental_home=.46,p_market_aware_home=.57,p_market_home=.62)
    assert r.market_flip
    assert r.fundamental_pick=="away"
    assert r.market_aware_pick=="home"
    assert r.market_influence==pytest.approx(.11)

def test_expected_upsets_do_not_force_pick_balance():
    rows=[
      compare_binary(event_id="a",p_fundamental_home=.60,p_market_aware_home=.60,p_market_home=.60),
      compare_binary(event_id="b",p_fundamental_home=.60,p_market_aware_home=.60,p_market_home=.60)]
    a=slate_audit(rows)
    assert a["market_aware_favorites"]==2
    assert a["expected_upsets_market_aware"]==pytest.approx(.8)
    assert a["authority_changed"] is False

def test_poisson_binomial_distribution():
    rows=[
      compare_binary(event_id="a",p_fundamental_home=.75,p_market_aware_home=.75,p_market_home=.75),
      compare_binary(event_id="b",p_fundamental_home=.75,p_market_aware_home=.75,p_market_home=.75)]
    assert upset_count_distribution(rows)==pytest.approx([.5625,.375,.0625])

def test_fundamentals_reject_market_features():
    with pytest.raises(ValueError):
        assert_fundamentals_market_free(["starter_quality","market_home_prob"])
    assert_fundamentals_market_free(["starter_quality","lineup_quality","rest_days"])

def test_active_vs_diagnostic():
    out=active_vs_diagnostic(["rest_days"],["rest_days","injury_uncertainty"])
    assert out["active_model_signals"]==["rest_days"]
    assert out["diagnostic_only_signals"]==["injury_uncertainty"]
