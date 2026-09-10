import pandas as pd
import pytest

from ks1.train import split, select_features, record_baseline, reliability


def test_holdout_is_later_and_unlabeled_games_excluded():
    frame = pd.DataFrame({'game_id': ['1', '2', '3'], 'date': ['2025-10-31', '2026-03-25', '2026-09-09'],
                          'home_win': [True, False, None]})
    train, test = split(frame)
    assert train.game_id.tolist() == ['1'] and test.game_id.tolist() == ['2']
    with pytest.raises(ValueError, match='duplicate'):
        split(pd.concat([frame, frame.iloc[:1]]))


def test_feature_selection_uses_training_only_and_excludes_labels():
    train = pd.DataFrame({'observed': [1., 2.], 'future_only': [None, None], 'home_score': [3., 4.]})
    dictionary = [{'column': 'observed', 'role': 'feature'}, {'column': 'future_only', 'role': 'feature'},
                  {'column': 'home_score', 'role': 'label_only'}]
    features, excluded = select_features(train, dictionary)
    assert features == ['observed'] and excluded == ['future_only']


def test_record_baseline_excludes_same_day_and_later_completion():
    targets = pd.DataFrame([{'game_id': '4', 'date': '2026-06-03', 'season': 2026,
                             'home_id': '1', 'away_id': '2', 'as_of_timestamp': '2026-06-03T19:50:00Z'}])
    old = {'game_id': '1', 'date': '2026-06-01', 'season': 2026, 'home_id': '1', 'away_id': '2',
           'home_win': 1, 'completed_at': '2026-06-01T23:00:00Z'}
    history = pd.DataFrame([old, {**old, 'game_id': '2', 'home_win': 0, 'completed_at': '2026-06-04T23:00:00Z'},
                            {**old, 'game_id': '3', 'date': '2026-06-03', 'completed_at': '2026-06-03T18:00:00Z'}])
    row = record_baseline(targets, history).iloc[0]
    assert row.home_record_games == row.away_record_games == 1
    assert row.better_record_p_home == pytest.approx(2/3)


def test_reliability_accounts_for_empty_buckets_and_endpoint_one():
    buckets = reliability([0, 1, 1], [0., .5, 1.])
    assert sum(r['count'] for r in buckets) == 3
    assert buckets[-1]['home_win_rate'] == 1
    assert buckets[2]['mean_p_home'] is None
