from copy import deepcopy
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ks1.development import digest, frozen_split, select


def frame():
    start = pd.Timestamp('2020-01-01', tz='UTC')
    rows = []
    for i in range(1000):
        when = start + pd.Timedelta(days=i)
        rows.append({'game_id': str(i), 'date': when.date().isoformat(),
                     'as_of_timestamp': when.isoformat(),
                     'label_completed_at': (when + pd.Timedelta(hours=4)).isoformat(),
                     'home_win': i % 2, 'home_score': 2 if i % 2 else 0, 'away_score': 1,
                     'signal': (i % 2) * .4 + (i % 7) * .01,
                     'noise': (i % 13) / 13})
    data = pd.DataFrame(rows)
    test = data.tail(300)
    manifest = {'game_ids': test.game_id.tolist(),
                'labels_sha256': digest(test.home_win.tolist()),
                'as_of_sha256': digest(test.as_of_timestamp.tolist()),
                'completed_sha256': digest(test.label_completed_at.tolist())}
    return data, manifest


def test_frozen_cohort_cannot_slide_or_admit_overlapping_training_labels():
    data, manifest = frame()
    data.loc[699, 'label_completed_at'] = data.loc[700, 'label_completed_at']
    newer = data.iloc[-1:].assign(game_id='new', label_completed_at='2025-01-02T00:00:00+00:00',
                                as_of_timestamp='2025-01-01T00:00:00+00:00')
    train, test = frozen_split(pd.concat([data, newer], ignore_index=True), manifest)
    assert test.game_id.tolist() == manifest['game_ids']
    assert '699' not in train.game_id.tolist() and 'new' not in train.game_id.tolist()
    assert len(train) == 699
    data.loc[999, 'home_win'] = 1 - data.loc[999, 'home_win']
    with pytest.raises(ValueError, match='frozen holdout changed'):
        frozen_split(data, manifest)


def test_selection_is_independent_of_final_holdout_values():
    data, manifest = frame()
    train, _ = frozen_split(data, manifest)
    recipes = {'starter': ['signal'], 'starter_plus_batters': ['signal', 'noise'],
               'starter_plus_batters_and_bullpen': ['signal', 'noise']}
    first = select(train, recipes)
    changed = data.copy()
    changed.loc[700:, ['signal', 'noise']] = np.nan
    changed.loc[700:, 'home_win'] = 1 - changed.loc[700:, 'home_win']
    alternate = deepcopy(manifest)
    alternate['labels_sha256'] = digest(changed.tail(300).home_win.tolist())
    other_train, _ = frozen_split(changed, alternate)
    assert select(other_train, recipes) == first
    assert first[1]['final_holdout_used_for_selection'] is False
    assert len(first[1]['trials']['starter']['trials']) == 4


def test_repository_manifest_is_bound_to_the_existing_300_game_experiment():
    manifest = json.loads((Path(__file__).parents[2]/'ks1/qualification_holdout_20260914.json').read_bytes())
    assert manifest['source_run'] == 34892538641
    assert len(manifest['game_ids']) == len(set(manifest['game_ids'])) == 300


def test_missing_matchups_defer_before_incumbent_or_holdout_scoring(monkeypatch, tmp_path):
    from ks1 import retrain_recent
    data, _ = frame()
    monkeypatch.setattr(retrain_recent, 'qualified_training_population',
                        lambda train, receipts: (train, {'retained_games': len(train)}))
    monkeypatch.setattr(retrain_recent, 'choose_features', lambda train: (['signal', 'noise'], [], {}))
    # Invalid model bytes would raise if evaluation touched the incumbent.
    report = retrain_recent.evaluate(data, b'not-a-booster', tmp_path,
                                    {'input_table_sha256': 'test'}, development_search=True)
    assert not report['accepted'] and not report['qualification_run']
    assert report['reason'] == 'matchup_values_unavailable_in_development_fit'
    assert not (tmp_path/'test_predictions.parquet').exists()
    assert (tmp_path/'development_selection.json').exists()
