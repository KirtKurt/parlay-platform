import numpy as np
import pandas as pd
import pytest
from scipy.stats import poisson

from ks1.poisson import align_reference, export_estimator, home_probability, make_estimator, predict_exported, verify_reference


def test_win_probability_matches_independent_score_enumeration():
    scores = np.arange(100)
    h, a = poisson.pmf(scores, 5), poisson.pmf(scores, 3)
    expected = np.sum(h[:, None] * a[None, :] * (scores[:, None] > scores[None, :])) + .5 * np.dot(h, a)
    p, tie = home_probability(5, 3)
    assert p == pytest.approx(expected, abs=1e-12)
    assert tie == pytest.approx(np.dot(h, a), abs=1e-12)
    assert p + home_probability(3, 5)[0] == pytest.approx(1)
    assert home_probability(4, 4)[0] == pytest.approx(.5)
    with pytest.raises(ValueError, match='positive'):
        home_probability(0, 4)


def test_preprocessing_stays_fitted_to_training_and_json_predictions_match():
    train = pd.DataFrame({'x': [1., 2., np.nan, 4., 5., 6.], 'z': [4., 5., 6., 7., 8., 9.]})
    estimator = make_estimator().fit(train, [1, 2, 0, 3, 4, 6])
    model = export_estimator(estimator, ['x', 'z'])
    test = pd.DataFrame({'x': [np.nan, 100.], 'z': [np.nan, -10.]})
    assert model['medians'] == [4., 6.5]
    np.testing.assert_allclose(predict_exported(model, test), estimator.predict(test), rtol=1e-12)
    assert model == export_estimator(estimator, ['x', 'z'])


def test_comparison_requires_identical_games_and_labels():
    test = pd.DataFrame({'game_id': ['1', '2'], 'date': ['2026-04-01', '2026-04-02'], 'season': [2026, 2026],
                         'home_team': ['A', 'B'], 'away_team': ['B', 'A'], 'home_win': [1, 0]})
    assert align_reference(test, test.iloc[::-1]).game_id.tolist() == ['1', '2']
    bad = test.copy(); bad.loc[0, 'home_win'] = 0
    with pytest.raises(ValueError, match='home_win'):
        align_reference(test, bad)
    with pytest.raises(ValueError, match='IDs'):
        align_reference(test, test.iloc[:1])


def test_reference_rejects_unaccepted_receipt(tmp_path):
    (tmp_path / 'artifact.json').write_text('{}')
    with pytest.raises(ValueError, match='accepted Phase 2'):
        verify_reference(tmp_path)
