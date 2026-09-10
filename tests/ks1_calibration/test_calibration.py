from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from scipy.special import logit
from sklearn.linear_model import LogisticRegression

from ks1 import daily, platt
from ks1.inventory import encode
from ks1.platt_inputs import dataset
from tests.ks1_phase5.test_refresh import capture


def rows(n):
    result = []
    base = datetime(2025, 1, 1, 10, tzinfo=timezone.utc)
    for i in range(n):
        at = base+timedelta(days=i)
        result.append({'game_id': str(i), 'as_of': at.isoformat(),
            'locked_at': (at+timedelta(hours=9)).isoformat(),
            'graded_at': (at+timedelta(hours=14)).isoformat(),
            'p_raw': .95 if i % 2 else .05, 'home_win': int(i % 3 != 0),
            'raw_model_version': platt.raw_model_version()})
    return result


NOW = '2026-09-10T06:00:00Z'


def test_no_locked_data_is_explicitly_unfitted_with_no_numeric_metrics():
    model, decision = platt.refit([], platt.identity(), NOW)
    assert (model['A'], model['B'], model['n'], model['fitted_at']) == (1, 0, 0, None)
    assert decision['status'] == 'waiting_for_7_new_graded_picks'
    assert all(r['n'] == 0 and r['brier'] is None and r['logloss'] is None
               for r in platt.compare([], NOW)['table'])


def test_seven_distinct_graded_picks_trigger_once_and_duplicates_cannot_count():
    old = platt.identity()
    assert platt.refit(rows(6), old, NOW)[0]['fitted_at'] is None
    model, report = platt.refit(rows(7), old, NOW)
    assert report['status'] == 'accepted'
    assert platt.refit(rows(7), model, NOW)[0] == model
    assert platt.refit(rows(13), model, NOW)[1]['new_graded_picks'] == 6
    assert platt.refit(rows(14), model, NOW)[1]['new_graded_picks'] == 7
    with pytest.raises(ValueError, match='duplicate'):
        platt.refit(rows(7)+rows(1), old, NOW)


def test_single_logit_column_c_and_exact_small_sample_parameter_shrink():
    data = rows(14)
    old = platt.identity()
    _, report = platt.refit(data, old, NOW)
    x = logit([r['p_raw'] for r in data]).reshape(-1, 1)
    fit = LogisticRegression(C=.3, solver='lbfgs', max_iter=2000, tol=1e-9).fit(x, [r['home_win'] for r in data])
    assert report['C'] == .3 and report['train_n'] == 14
    assert report['candidate_A'] == pytest.approx(.8+.2*fit.coef_[0, 0])
    assert report['candidate_B'] == pytest.approx(.2*fit.intercept_[0])


def test_rolling_last_100_and_c_switch_ignore_older_labels():
    data = rows(120)
    first, report = platt.refit(data, platt.identity(), NOW)
    assert report['C'] == 1 and report['shrink_old_weight'] == 0
    assert report['train_game_ids'] == [str(i) for i in range(20, 120)]
    assert report['gate']['game_ids'] == [str(i) for i in range(70, 120)]
    for row in data[:20]:
        row['home_win'] = 1-row['home_win']
    second, _ = platt.refit(data, platt.identity(), NOW)
    assert (first['A'], first['B']) == (second['A'], second['B'])


def test_regression_rejects_candidate_and_keeps_all_previous_fit_metadata(monkeypatch):
    data = rows(14)
    for r in data:
        r['home_win'] = int(r['p_raw'] > .5)
    class BadFit:
        coef_ = np.array([[-50.]])
        intercept_ = np.array([10.])
        def fit(self, x, y):
            assert x.shape[1] == 1
            return self
    monkeypatch.setattr(platt, 'LogisticRegression', lambda **kwargs: BadFit())
    old = dict(platt.identity(), A=1., B=0., n=7, fitted_at='2024-12-31T01:00:00Z')
    kept, report = platt.refit(data, old, NOW)
    assert report['status'] == 'worse_brier_keep_prior'
    assert all(kept[k] == old[k] for k in ('A', 'B', 'n', 'fitted_at'))
    assert platt.refit(data, kept, NOW)[1]['status'] == 'waiting_for_7_new_graded_picks'


def test_comparison_is_chronological_and_a_label_cannot_predict_itself():
    data = rows(65)
    report = platt.compare(data, NOW)
    assert all(r['n'] == 50 for r in report['table'])
    by_id = {r['game_id']: r for r in data}
    for d in report['decisions']:
        assert d['test_game_id'] not in d['train_game_ids']
        assert all(by_id[g]['graded_at'] < by_id[d['test_game_id']]['as_of'] for g in d['train_game_ids'])
    changed = deepcopy(data)
    changed[-1]['home_win'] = 1-changed[-1]['home_win']
    again = platt.compare(changed, NOW)
    last = report['predictions'][-1]
    last_again = again['predictions'][-1]
    assert [last[k] for k in ('raw', 'temperature', 'platt')] == [last_again[k] for k in ('raw', 'temperature', 'platt')]


def test_temperature_installed_separately_with_cadence_and_positive_t():
    model, report = platt.refit_temperature(rows(35), platt.temperature_identity(), NOW)
    assert report['status'] == 'accepted' and .05 <= model['T'] <= 20 and model['n'] == 35
    assert platt.refit_temperature(rows(35), model, NOW)[0] == model
    assert 'A' not in model and 'B' not in model
    with pytest.raises(ValueError, match='temperature must'):
        platt.apply_temperature([.5], {'T': 0})


def publication_rows():
    row = {'game_id': 'one', 'date': '2026-09-10', 'as_of': '2026-09-10T05:00:00Z',
           'commence_time': '2026-09-10T20:00:00Z', 'model_version': platt.raw_model_version(),
           'p_home': .8, 'market_home_prob': .6, 'edge_home': .2, 'lambda_home': 5.,
           'lambda_away': 4., 'p_home_poisson': .61, 'proj_total': 9., 'status': 'projected'}
    return [row, dict(row, game_id='frozen', commence_time='2026-09-10T05:55:00Z')]


def test_publish_only_preserves_frozen_rows_and_keeps_raw_for_recalibration():
    source = publication_rows()
    model = dict(platt.identity(), A=.5, B=.1, n=14, fitted_at=NOW)
    calibrated, changed = platt.prepare_rows(source, model, NOW)
    assert changed == ['one'] and calibrated[1] == source[1]
    assert calibrated[0]['p_raw'] == .8 and calibrated[0]['p_home'] != .8
    assert all(calibrated[0][k] == source[0][k] for k in ('lambda_home', 'lambda_away', 'proj_total', 'p_home_poisson'))
    assert platt.prepare_rows(calibrated, model, '2026-09-10T06:01:00Z') == (calibrated, [])
    temperature = dict(platt.temperature_identity(), T=2., n=30, fitted_at=NOW)
    switched, _ = platt.prepare_rows(calibrated, temperature, '2026-09-10T06:01:00Z', 'temperature')
    assert switched[0]['p_home'] == pytest.approx(float(platt.apply_temperature(.8, temperature)))
    assert switched[1] == source[1]  # Neither stack transforms nor rewrite locks.


def test_future_models_and_missing_raw_are_rejected():
    model = dict(platt.identity(), A=.5, B=.1, n=14, fitted_at='2026-09-11T06:00:00Z')
    with pytest.raises(ValueError, match='future'):
        platt.prepare_rows(publication_rows(), model, NOW)
    model['fitted_at'] = NOW
    bad = publication_rows(); bad[0]['calibration_version'] = 'older'; bad[0]['p_raw'] = None
    with pytest.raises(ValueError, match='original raw'):
        platt.prepare_rows(bad, model, NOW)


def test_prediction_stage_remains_raw_and_identity_publisher_preserves_it(capture, monkeypatch):
    folder, output, _, _ = capture
    monkeypatch.setattr(platt, 'prepare_rows', lambda *a: pytest.fail('calibration applied during scoring'))
    table, _, _ = daily.predict(folder, output)
    assert all(r['p_home'] == .55 and r['p_raw'] is None for r in table.to_pylist())


def captured_locked():
    row = publication_rows()[0]
    final = {'home_id': '1', 'away_id': '2', 'home_score': 5, 'away_score': 4,
             'completed_at': '2026-09-10T23:00:00Z', 'observed_at': '2026-09-10T23:05:00Z'}
    row.update(home_id='1', away_id='2')
    return {'system': 'KS1', 'as_of': '2026-09-11T06:00:00Z', 'finals': {'one': final},
            'final_sources': [{'sha256': 'a'*64}], 'locked': [{'row': row,
                'evidence': {'version_id': 'actual-version', 'stored_at': '2026-09-10T05:01:00Z', 'sha256': 'a'*64}}]}


def test_only_verified_locked_original_lgb_probabilities_are_admitted():
    source = captured_locked()
    admitted, report = dataset(source)
    assert len(admitted) == 1 and admitted[0]['p_raw'] == .8 and admitted[0]['home_win'] == 1
    assert admitted[0]['graded_at'] == '2026-09-10T23:05:00+00:00'
    source['locked'][0]['evidence']['stored_at'] = '2026-09-10T22:00:00Z'
    with pytest.raises(ValueError, match='prospective'):
        dataset(source)
    source = captured_locked(); source['locked'][0]['row']['official_probability_field'] = 'p_home_sim'
    assert dataset(source)[0] == []


def test_first_label_availability_is_retained_and_corrections_cannot_silently_refit():
    source = captured_locked()
    admitted, _ = dataset(source)
    state, _ = platt.refit(admitted, platt.identity(), source['as_of'])
    source['platt_model'] = state
    source['as_of'] = '2026-09-12T06:00:00Z'
    source['finals']['one']['observed_at'] = '2026-09-12T05:00:00Z'
    assert dataset(source)[0][0]['graded_at'] == admitted[0]['graded_at']
    source['finals']['one']['away_score'] = 6
    with pytest.raises(ValueError, match='observation changed'):
        dataset(source)


def test_unproven_old_final_availability_is_not_backdated():
    source = captured_locked(); source['finals']['one'].pop('observed_at')
    assert dataset(source)[0][0]['graded_at'] == '2026-09-11T06:00:00+00:00'
