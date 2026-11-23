from __future__ import annotations

from mlb_auto.features import bootstrap_home_probability
from mlb_auto.ml import Model
from mlb_auto.model_guard import (
    FALLBACK_MODE,
    MODEL_GUARD_VERSION,
    evaluate_model_input,
    policy_payload,
)
from mlb_auto.model_guard_runtime import _GuardedChampion


def _model(*, names=('x',), means=None, scales=None, weights=None):
    feature_names = tuple(names)
    return Model(
        intercept=0.0,
        weights=tuple(weights or [1.0] * len(feature_names)),
        feature_names=feature_names,
        metadata={
            'feature_means': dict(means or {name: 0.0 for name in feature_names}),
            'feature_scales': dict(scales or {name: 1.0 for name in feature_names}),
        },
    )


def test_guard_allows_supported_inputs():
    result = evaluate_model_input(_model(names=('x', 'y')), {'x': 1.5, 'y': -2.0})

    assert result['triggered'] is False
    assert result['reason'] == 'IN_RANGE'
    assert result['features_evaluated'] == 2
    assert result['max_abs_z'] == 2.0


def test_guard_triggers_on_one_extreme_selected_feature():
    result = evaluate_model_input(_model(), {'x': 6.01})

    assert result['triggered'] is True
    assert result['fallback_required'] is True
    assert result['reason'] == 'SINGLE_FEATURE_EXTREME'
    assert result['out_of_range_features'][0]['name'] == 'x'
    assert result['out_of_range_features'][0]['abs_z'] > 6.0


def test_guard_triggers_on_multiple_material_outliers():
    result = evaluate_model_input(
        _model(names=('x', 'y', 'z')),
        {'x': 4.2, 'y': -4.1, 'z': 0.2},
    )

    assert result['triggered'] is True
    assert result['reason'] == 'MULTIPLE_FEATURES_OUT_OF_RANGE'
    assert {row['name'] for row in result['out_of_range_features']} == {'x', 'y'}


def test_guard_fails_closed_for_missing_selected_feature():
    result = evaluate_model_input(_model(names=('x', 'y')), {'x': 0.0})

    assert result['triggered'] is True
    assert result['reason'] == 'INVALID_OR_MISSING_SELECTED_FEATURE'
    assert result['invalid_features'] == ['y']


def test_guard_understands_interaction_feature_inputs():
    model = _model(
        names=('x__x__y',),
        means={'x__x__y': 0.0},
        scales={'x__x__y': 1.0},
    )
    result = evaluate_model_input(model, {'x': 1.0, 'y': 2.0})

    assert result['triggered'] is False
    assert result['features_evaluated'] == 1


def test_guarded_champion_falls_back_without_clipping_raw_feature():
    model = _model(
        names=('hours_to_first_pitch',),
        means={'hours_to_first_pitch': 0.75},
        scales={'hours_to_first_pitch': 0.05},
        weights=(8.0,),
    )
    features = {
        'hours_to_first_pitch': 20.0,
        'market_home_probability': 0.55,
        'market_move': 0.0,
        'book_divergence': 0.0,
        'market_reversals': 0.0,
    }

    guarded_probability = _GuardedChampion(model).predict(features)

    assert guarded_probability == bootstrap_home_probability(features)
    assert features['hours_to_first_pitch'] == 20.0
    assert guarded_probability < 0.60


def test_policy_is_explicit_and_versioned():
    policy = policy_payload()

    assert policy['enabled'] is True
    assert policy['version'] == MODEL_GUARD_VERSION
    assert policy['fallback_mode'] == FALLBACK_MODE
    assert policy['official_pick_blocked_when_triggered'] is True
