import lightgbm as lgb
import numpy as np
import pandas as pd
import pytest

from ks1.brier_objective import _probability
from ks1.train_brier import export_probability_model, fit_brier, metrics, split_september, evaluate


def test_real_lightgbm_46_train_and_native_probability_export(tmp_path):
    x = pd.DataFrame({'signal': [-3., -2., -1., 1., 2., 3.] * 40})
    y = np.asarray([0, 0, 0, 1, 1, 1] * 40)
    booster, expected = fit_brier(x, y, x, ['signal'], rounds=8)
    raw = booster.predict(x, raw_score=True)
    assert raw.min() < 0 < raw.max()
    path = tmp_path / 'model.txt'
    export_probability_model(booster, path, x)
    # This is precisely the unwrapped serving API: no extra sigmoid at call site.
    loaded = lgb.Booster(model_file=str(path))
    actual = loaded.predict(x)
    np.testing.assert_allclose(actual, expected, atol=1e-12, rtol=0)
    np.testing.assert_allclose(loaded.predict(x, raw_score=True), raw, atol=1e-12, rtol=0)
    assert ((actual >= 0) & (actual <= 1)).all()
    with pytest.raises(ValueError, match='unexpected custom-objective'):
        export_probability_model(loaded, tmp_path / 'invalid.txt', x)


@pytest.mark.parametrize('y,p', [([0,1],[.2,.8]), ([1,1],[.8,.8]), ([0,0],[.2,.2])])
def test_logloss_is_recorded_even_for_single_class_holdout(y, p):
    assert metrics(y, p)['logloss'] == pytest.approx(-np.log(.8))


@pytest.mark.parametrize('p', [[np.nan], [np.inf], [-.1], [1.1]])
def test_invalid_probabilities_rejected(p):
    with pytest.raises(ValueError, match='invalid probability'):
        metrics([1], p)


def frame():
    return pd.DataFrame([{
        'game_id': str(i), 'date': '2026-08-30' if i < 501 else '2026-09-02',
        'as_of_timestamp': '2026-08-30T16:00:00Z' if i < 501 else '2026-09-02T16:00:00Z',
        'label_completed_at': '2026-08-30T20:00:00Z' if i < 501 else '2026-09-02T20:00:00Z',
        'home_win': i % 2, 'home_score': 4, 'away_score': 3,
    } for i in range(601)])


def test_fixed_september_split_excludes_delayed_august_labels_and_october():
    data = frame()
    data.loc[0, 'label_completed_at'] = '2026-09-01T05:00:00Z'
    extra = data.iloc[-1:].copy()
    extra['game_id'] = 'october'; extra['date'] = '2026-10-01'
    extra['as_of_timestamp'] = '2026-10-01T16:00:00Z'
    extra['label_completed_at'] = '2026-10-01T20:00:00Z'
    train, test = split_september(pd.concat([data, extra], ignore_index=True))
    assert len(train) == 500 and len(test) == 100
    assert '0' not in set(train.game_id)
    assert 'october' not in set(test.game_id)
    assert set(train.game_id).isdisjoint(test.game_id)


@pytest.mark.parametrize('column,value', [('label_completed_at', None), ('as_of_timestamp', None),
                                         ('home_win', 2), ('game_id', '1')])
def test_invalid_holdout_inputs_fail_closed(column, value):
    data = frame(); data.loc[0, column] = value
    with pytest.raises(ValueError):
        split_september(data)


def test_wrong_incumbent_rejected_before_training_or_output(tmp_path):
    with pytest.raises(ValueError, match='incumbent does not match'):
        evaluate(frame(), b'wrong model', tmp_path / 'output', 'test')
    assert not (tmp_path / 'output').exists()
