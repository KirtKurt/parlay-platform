import pytest
from nfl_auto.independent_intelligence import split_feature_vector, MARKET_FEATURES
from nfl_auto.features import FEATURE_NAMES
from ks1.independent_intelligence import fundamental_feature_names
from soccer_auto.independent_intelligence import compare_grids
from soccer_auto.kss1_markets import score_matrix
from tennis_independent_intelligence import compare_match

def test_nfl_market_features_are_separated():
    out=split_feature_vector([0.0]*len(FEATURE_NAMES))
    assert set(out["market"])==MARKET_FEATURES
    assert not set(out["fundamentals"]) & MARKET_FEATURES

def test_ks1_market_home_prob_cannot_enter_fundamentals():
    names=fundamental_feature_names(["starter_era_30d","market_home_prob","rest_days"])
    assert names==("starter_era_30d","rest_days")

def test_soccer_preserves_three_way_market_flip_diagnostics():
    grid=score_matrix(1.8,0.7)
    out=compare_grids(event_id="s1",fundamental_grid=grid,
                      market_1x2={"home":.20,"draw":.20,"away":.60})
    assert set(out["market_influence_by_outcome"])=={"home","draw","away"}
    assert out["authority_changed"] is False

def test_tennis_adapter_never_forces_dog():
    out=compare_match(event_id="t1",player_a="A",player_b="B",
       p_fundamental_a=.55,p_market_aware_a=.56,p_market_a=.60)
    assert out.market_flip is False
    assert out.favorite_selected_market_aware is True
