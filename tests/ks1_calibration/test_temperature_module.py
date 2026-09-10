from copy import deepcopy

import pytest

from src import temperature_calibrator as tc
from tests.ks1_calibration.test_calibration import rows, NOW


def test_exact_identity_and_30_row_minimum(tmp_path):
    path = tmp_path/'temperature.json'
    model, report = tc.fit_from_ledger(rows(29), model_path=path, as_of=NOW)
    assert (model['T'], model['n'], model['fitted_at']) == (1., 0, None)
    for p in (0, .1, .25, .5, .75, .9, 1):
        assert tc.calibrate_p(p, model_path=path) == p
    fitted, report = tc.fit_from_ledger(rows(30), model_path=path, as_of=NOW)
    assert fitted['n'] == 30 and fitted['T'] > 1
    assert report['candidate_T'] == .8*model['T']+.2*report['fitted_T']


def test_only_last_100_and_repeat_is_noop(tmp_path):
    data = rows(140)
    path = tmp_path/'temperature.json'
    first, report = tc.fit_from_ledger(data, model_path=path, as_of=NOW)
    assert first['n'] == 100 and report['game_ids'] == [str(i) for i in range(40, 140)]
    body = path.read_bytes()
    for r in data[:40]:
        r['home_win'] = 1-r['home_win']
    repeat, report = tc.fit_from_ledger(data, model_path=path, as_of=NOW)
    assert repeat == first and path.read_bytes() == body
    assert report['status'] == 'unchanged_ledger_no_refit'


def test_logloss_regression_rejects_even_when_brier_improves(monkeypatch):
    monkeypatch.setattr(tc, '_fit_t', lambda rows: 5.)
    monkeypatch.setattr(tc, '_metrics', lambda rows, t: {'n': len(rows),
                        'brier': .20 if t == 1 else .19, 'logloss': .5 if t == 1 else .6})
    old = tc.identity()
    result, report = tc.fit_from_ledger(rows(35), previous=old, as_of=NOW, persist=False)
    assert report['status'] == 'metric_regression_keep_prior'
    assert all(result[k] == old[k] for k in ('T', 'n', 'fitted_at'))


def test_duplicate_or_ungraded_rows_are_not_a_temperature_ledger():
    with pytest.raises(ValueError, match='duplicate'):
        tc.fit_from_ledger(rows(30)+rows(1), previous=tc.identity(), as_of=NOW, persist=False)
    invalid = rows(30)
    invalid[-1]['graded_at'] = '2027-01-01T00:00:00Z'
    with pytest.raises(ValueError, match='graded official locks'):
        tc.fit_from_ledger(invalid, previous=tc.identity(), as_of=NOW, persist=False)


def test_nonidentity_artifact_cannot_bypass_minimum():
    with pytest.raises(ValueError, match='30 fitted'):
        tc.validate_model({'T': 2., 'n': 29, 'fitted_at': NOW})
