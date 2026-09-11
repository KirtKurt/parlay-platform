import pandas as pd
import pytest
from ks1.features import Features
from ks1.retrain_recent import accepted, choose_features, split_recent
from tests.ks1.test_game_table import game


def test_seven_day_calendar_boundary_and_future_exclusion():
    games = [game(1, '2026-08-01'), game(2, '2026-08-02'), game(3, '2026-08-08'),
             game(4, '2026-08-09', hits=25), game(5, '2026-08-07', completed='2026-08-10T23:00:00Z')]
    row = Features(games).at('2026-08-09T19:00:00Z', '1')
    assert row['offense_games_7d'] == 2
    assert row['offense_games_10d'] == 3
    assert row['offense_pa_7d'] == 66
    assert row['team_starter_bf_7d'] == 48


def test_split_has_no_overlap_and_requires_labels_and_counts():
    rows = [{'game_id': str(i), 'date': '2026-08-31' if i < 500 else '2026-09-01',
             'home_win': i % 2, 'home_score': 3, 'away_score': 2,
             'label_completed_at': '2026-08-31T23:00:00Z' if i < 500 else '2026-09-02T02:00:00Z'} for i in range(600)]
    frame = pd.DataFrame(rows)
    frame.loc[0, 'label_completed_at'] = '2026-08-31T23:00:00.123456+00:00'
    train, test = split_recent(frame)
    assert len(train) == 500 and len(test) == 100
    assert train.date.max() < test.date.min()
    with pytest.raises(ValueError, match='duplicate'):
        split_recent(pd.concat([frame, frame.iloc[:1]]))
    frame.loc[599, 'home_score'] = None
    with pytest.raises(ValueError, match='insufficient'):
        split_recent(frame)


def test_no_individual_starter_learning_from_unobserved_ids_or_prior_only():
    frame = pd.DataFrame({'home_offense_ops_7d': [0.5, 0.6],
                          'home_starter_id': [None, None], 'away_starter_id': [None, None],
                          'home_starter_bf_30d': [0., 1.], 'away_starter_bf_30d': [0., 1.],
                          'home_actual_starter_id': ['1', '2'], 'home_score': [1, 2]})
    features, _, coverage = choose_features(frame)
    assert features == ['home_offense_ops_7d']
    assert coverage == {'home': 0, 'away': 0}


def test_promotion_requires_both_probability_metrics_and_same_sufficient_cohort():
    old = {'games': 100, 'brier': .24, 'logloss': .68}
    assert accepted({'games': 100, 'brier': .23, 'logloss': .67}, old)
    assert not accepted({'games': 100, 'brier': .23, 'logloss': .69}, old)
    assert not accepted(old, old)
    assert not accepted({'games': 99, 'brier': .23, 'logloss': .67}, old)


def test_august_game_completed_after_holdout_start_cannot_train():
    rows = [{'game_id': str(i), 'date': '2026-08-31' if i < 501 else '2026-09-01',
             'home_win': i % 2, 'home_score': 3, 'away_score': 2,
             'label_completed_at': '2026-08-31T23:00:00Z' if i < 500 else '2026-09-02T02:00:00Z'} for i in range(601)]
    train, test = split_recent(pd.DataFrame(rows))
    assert len(train) == 500 and len(test) == 100
    assert '500' not in set(train.game_id)


def test_training_rejects_features_unavailable_at_serving():
    frame = pd.DataFrame({'home_offense_ops_7d': [0.5, 0.6], 'temp': [65., 75.],
                          'home_starter_id': [None, None], 'away_starter_id': [None, None],
                          'home_starter_bf_30d': [0., 0.], 'away_starter_bf_30d': [0., 0.]})
    with pytest.raises(ValueError, match='missing from daily inference: temp'):
        choose_features(frame)
