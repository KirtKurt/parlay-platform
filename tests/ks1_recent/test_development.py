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
                     'home_starter_id': None, 'away_starter_id': None,
                     'home_starter_bf_30d': None, 'away_starter_bf_30d': None,
                     'home_win': i % 2, 'home_score': 2 if i % 2 else 0, 'away_score': 1,
                     'home_offense_ops_7d': (i % 2) * .4 + (i % 7) * .01,
                     'away_offense_ops_7d': (i % 13) / 13})
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
    first = select(train)
    changed = data.copy()
    changed.loc[700:, ['home_offense_ops_7d', 'away_offense_ops_7d']] = np.nan
    changed.loc[700:, 'home_win'] = 1 - changed.loc[700:, 'home_win']
    alternate = deepcopy(manifest)
    alternate['labels_sha256'] = digest(changed.tail(300).home_win.tolist())
    other_train, _ = frozen_split(changed, alternate)
    assert select(other_train) == first
    assert first[1]['final_holdout_used_for_selection'] is False
    assert len(first[1]['trials']['starter']['trials']) == 5


def test_repository_manifest_is_bound_to_the_existing_300_game_experiment():
    manifest = json.loads((Path(__file__).parents[2]/'ks1/qualification_holdout_20260914.json').read_bytes())
    assert manifest['source_run'] == 34892538641
    assert len(manifest['game_ids']) == len(set(manifest['game_ids'])) == 300


def test_development_tail_cannot_satisfy_feature_admission_threshold():
    from ks1.retrain_recent import choose_features, split_development
    data, manifest = frame()
    train, _ = frozen_split(data, manifest)
    column = 'home_lineup_ops_7d'
    train[column] = np.nan
    train.loc[train.index[:299], column] = np.arange(299) / 1000
    _, validation = split_development(train)
    train.loc[validation.index, column] = .9
    assert column in choose_features(train)[0]
    _, report = select(train)
    assert column in report['omitted_features']
    assert column not in report['trials']['starter_plus_batters']['features']
    train.loc[validation.index, column] = np.nan
    assert select(train)[1] == report


def test_matchup_admission_distinguishes_sparse_constant_and_admitted_values():
    from ks1.development import matchup_admission_report
    from ks1.retrain_recent import choose_features
    fit = frame()[0].iloc[:500].copy()
    sparse = 'home_lineup_pitch_type_matchup_whiff_pct_30d'
    enough = 'away_lineup_pitch_type_matchup_whiff_pct_30d'
    constant = 'home_lineup_pitch_type_matchup_xwoba_7d'
    absent = 'away_lineup_pitch_type_matchup_xwoba_7d'
    text_value = 'home_lineup_pitch_type_matchup_xwoba_30d'
    fit[sparse] = np.nan
    fit.loc[:298, sparse] = np.arange(299) / 1000
    fit[enough] = np.nan
    fit.loc[:299, enough] = np.arange(300) / 1000
    fit[constant] = .5
    fit[text_value] = ['not-numeric', 'also-not-numeric'] * 250
    before = fit.copy(deep=True)
    admitted, omitted, _ = choose_features(fit)
    report = matchup_admission_report(fit, admitted)
    assert len(report) == 12
    assert report[sparse]['nonmissing_games'] == 299
    assert report[sparse]['exclusion_reasons'] == ['below_development_fit_nonmissing_floor']
    assert report[enough]['nonmissing_games'] == 300
    assert report[enough]['admitted'] and report[enough]['exclusion_reasons'] == []
    assert report[constant]['exclusion_reasons'] == ['unavailable_or_constant']
    assert report[absent]['exclusion_reasons'] == ['column_absent']
    assert report[text_value]['exclusion_reasons'] == ['non_numeric']
    assert report[enough]['group'] == 'pitch_type_matchup'
    assert report['home_lineup_platoon_xwoba_7d']['group'] == 'platoon'
    assert sparse in omitted and enough in admitted
    pd.testing.assert_frame_equal(fit, before)


def test_matchup_report_cannot_use_development_tail_to_claim_coverage():
    data, manifest = frame()
    train, _ = frozen_split(data, manifest)
    column = 'home_lineup_pitch_type_matchup_whiff_pct_30d'
    train[column] = np.nan
    train.loc[train.index[:299], column] = np.arange(299) / 1000
    from ks1.retrain_recent import split_development
    _, validation = split_development(train)
    train.loc[validation.index, column] = .9
    _, report = select(train)
    entry = report['matchup_value_admission'][column]
    assert entry['nonmissing_games'] == 299 and not entry['admitted']
    assert entry['fit_games'] == report['fit_games']
    train.loc[validation.index, column] = np.nan
    assert select(train)[1]['matchup_value_admission'] == report['matchup_value_admission']


@pytest.mark.parametrize('bad', [2, -1, .5, '1'])
def test_frozen_training_population_rejects_nonbinary_labels(bad):
    data, manifest = frame()
    data['home_win'] = data.home_win.astype(object)
    data.loc[1, 'home_win'] = bad
    with pytest.raises(ValueError, match='training labels must be binary'):
        frozen_split(data, manifest)


def test_missing_matchups_defer_before_incumbent_or_holdout_scoring(monkeypatch, tmp_path):
    from ks1 import retrain_recent
    data, _ = frame()
    monkeypatch.setattr(retrain_recent, 'qualified_training_population',
                        lambda train, receipts: (train, {'retained_games': len(train)}))
    # Invalid model bytes would raise if evaluation touched the incumbent.
    report = retrain_recent.evaluate(data, b'not-a-booster', tmp_path,
                                    {'input_table_sha256': 'test'}, development_search=True)
    assert not report['accepted'] and not report['qualification_run']
    assert report['reason'] == 'matchup_values_unavailable_in_development_fit'
    assert not (tmp_path/'test_predictions.parquet').exists()
    assert (tmp_path/'development_selection.json').exists()
