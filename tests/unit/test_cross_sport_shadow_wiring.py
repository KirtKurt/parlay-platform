import pytest
from nfl_auto.independent_intelligence import (
    FUNDAMENTAL_FEATURE_NAMES,
    MARKET_FEATURES,
    NEUTRAL_PRIOR,
    fundamentals_only_probability,
    split_feature_vector,
    zero_market_slots,
)
from nfl_auto.features import FEATURE_NAMES
from nfl_auto.model import ResidualLogisticModel
from ks1.independent_intelligence import fundamental_feature_names
from soccer_auto.independent_intelligence import compare_grids
from soccer_auto.kss1_markets import score_matrix
from tennis_independent_intelligence import compare_match


def test_nfl_market_features_are_separated():
    out = split_feature_vector([0.0] * len(FEATURE_NAMES))
    assert set(out["market"]) == MARKET_FEATURES
    assert not set(out["fundamentals"]) & MARKET_FEATURES
    assert set(FUNDAMENTAL_FEATURE_NAMES).isdisjoint(MARKET_FEATURES)


def test_nfl_zero_market_slots_leaves_football_stats():
    values = [float(index + 1) for index in range(len(FEATURE_NAMES))]
    cleaned = zero_market_slots(values)
    for index, name in enumerate(FEATURE_NAMES):
        if name in MARKET_FEATURES:
            assert cleaned[index] == 0.0
        else:
            assert cleaned[index] == values[index]


def test_nfl_serving_probability_stays_on_full_vector_and_market_prior():
    model = ResidualLogisticModel.initialize("moneyline_home_win")
    model.weights = [0.1] * len(model.weights)
    features = [0.2] * len(FEATURE_NAMES)
    prior = 0.64
    published = model.predict_probability(features, prior)
    shadow = fundamentals_only_probability(model, features)
    assert published == model.predict_probability(features, prior)
    assert shadow != published
    assert shadow == model.predict_probability(zero_market_slots(features), NEUTRAL_PRIOR)


def test_ks1_market_home_prob_cannot_enter_fundamentals():
    names = fundamental_feature_names(["starter_era_30d", "market_home_prob", "rest_days"])
    assert names == ("starter_era_30d", "rest_days")


def test_soccer_preserves_three_way_market_flip_diagnostics():
    grid = score_matrix(1.8, 0.7)
    out = compare_grids(
        event_id="s1",
        fundamental_grid=grid,
        market_1x2={"home": 0.20, "draw": 0.20, "away": 0.60},
    )
    assert set(out["market_influence_by_outcome"]) == {"home", "draw", "away"}
    assert out["authority_changed"] is False


def test_tennis_adapter_never_forces_dog():
    out = compare_match(
        event_id="t1",
        player_a="A",
        player_b="B",
        p_fundamental_a=0.55,
        p_market_aware_a=0.56,
        p_market_a=0.60,
    )
    assert out.market_flip is False
    assert out.favorite_selected_market_aware is True
