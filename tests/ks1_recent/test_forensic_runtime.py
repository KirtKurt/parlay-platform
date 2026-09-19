"""Performance/progress changes must not become a different model experiment."""
from copy import deepcopy
import json
import warnings

import lightgbm as lgb
import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal
import pytest

import ks1.forensic_derived as subject
from ks1 import forensic_runtime as runtime
from ks1.forensic_derived_development import run


def legacy_augment(frame, derived):
    result = frame.copy()
    values = subject.derive_frame(frame)
    for column in derived:
        result[column] = values[column]
    return result


def install_values(monkeypatch, index, count=145):
    values = pd.DataFrame({f'derived_{i}': np.arange(len(index), dtype=float) + i
                           for i in range(count)}, index=index)
    if len(index):
        values.iloc[0, ::3] = np.nan
    monkeypatch.setattr(subject, 'derive_frame', lambda frame: values.copy())
    return values


@pytest.mark.parametrize('index', [pd.Index([9, 2, 8], name='row'),
                                  pd.Index([1, 1, 3]), pd.RangeIndex(0),
                                  pd.date_range('2025-01-01', periods=3, tz='UTC')])
def test_batch_augmentation_matches_legacy_and_preserves_input(monkeypatch, index):
    frame = pd.DataFrame({'a': np.arange(len(index), dtype=np.int64),
                          'text': ['unchanged'] * len(index)}, index=index)
    frame.columns.name = 'feature'
    frame.attrs = {'source': {'immutable': True}}
    original = frame.copy(deep=True)
    values = install_values(monkeypatch, index)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', pd.errors.PerformanceWarning)
        expected = legacy_augment(frame, list(values))
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always', pd.errors.PerformanceWarning)
        actual = subject._augment(frame, list(values))
    assert not any(issubclass(w.category, pd.errors.PerformanceWarning) for w in caught)
    assert_frame_equal(actual, expected, check_exact=True)
    assert actual.attrs == expected.attrs
    assert_frame_equal(frame, original, check_exact=True)
    actual.attrs['source']['immutable'] = False
    assert frame.attrs['source']['immutable'] is True
    if len(frame):
        actual.iloc[-1, 0] = 999
        assert_frame_equal(frame, original, check_exact=True)


def test_batch_removes_wide_frame_fragmentation_without_dropping_columns(monkeypatch):
    frame = pd.DataFrame({'raw': np.arange(100)})
    values = install_values(monkeypatch, frame.index)
    with warnings.catch_warnings(record=True) as old_warnings:
        warnings.simplefilter('always', pd.errors.PerformanceWarning)
        legacy_augment(frame, values.columns)
    assert any(issubclass(w.category, pd.errors.PerformanceWarning) for w in old_warnings)
    with warnings.catch_warnings():
        warnings.simplefilter('error', pd.errors.PerformanceWarning)
        actual = subject._augment(frame, values.columns)
    assert list(actual) == ['raw'] + list(values)


@pytest.mark.parametrize('selection', [[], ['derived_2', 'derived_0', 'derived_2'],
                                       ['derived_1', 'derived_0']])
def test_empty_and_repeated_selection_preserve_order(monkeypatch, selection):
    frame = pd.DataFrame({'raw': [1, 2, 3]})
    install_values(monkeypatch, frame.index, 3)
    assert_frame_equal(subject._augment(frame, selection), legacy_augment(frame, selection), check_exact=True)


@pytest.mark.parametrize('duplicate', [False, True])
def test_existing_derived_column_keeps_replacement_semantics(monkeypatch, duplicate):
    frame = pd.DataFrame([[99, 1, 2], [88, 3, 4]],
                         columns=['derived_0', 'raw', 'raw' if duplicate else 'other'])
    install_values(monkeypatch, frame.index, 3)
    selection = ['derived_1', 'derived_0', 'derived_2']
    assert_frame_equal(subject._augment(frame, selection), legacy_augment(frame, selection), check_exact=True)


def test_unknown_derived_column_still_fails_closed(monkeypatch):
    frame = pd.DataFrame({'raw': [1., 2.]})
    install_values(monkeypatch, frame.index, 2)
    with pytest.raises(KeyError):
        subject._augment(frame, ['unknown'])
    assert list(frame) == ['raw']


def test_multiindex_column_compatibility(monkeypatch):
    frame = pd.DataFrame([[1., 2.], [3., 4.]],
                         columns=pd.MultiIndex.from_tuples([('a', 'x'), ('b', 'y')]))
    values = pd.DataFrame({('c', 'z'): [5., 6.]})
    monkeypatch.setattr(subject, 'derive_frame', lambda frame: values.copy())
    assert_frame_equal(subject._augment(frame, [('c', 'z')]),
                       legacy_augment(frame, [('c', 'z')]), check_exact=True)


def progress(output):
    return json.loads((output / runtime.PROGRESS_FILE).read_text())


def test_scoped_trial_progress_preserves_identity_arguments_and_return(tmp_path, capsys):
    fit, valid, columns, params = [object()] * 5, [object()] * 2, ['secret_feature'], {'secret': 'secret_value'}
    expected = {'accepted': False, 'qualification_run': False}

    @runtime.trace_trial
    def trial(a, b, c, d):
        assert a is fit and b is valid and c is columns and d is params
        event = progress(tmp_path)
        assert event['stage'] == 'trial_started'
        assert event['trials_started'] == 1 and event['trials_completed'] == 0
        return expected

    @runtime.trace_development_run
    def invoke(input_path, proof_path, output):
        assert progress(output)['stage'] == 'development_started'
        result = trial(fit, valid, columns, params)
        assert progress(output)['stage'] == 'trial_completed'
        return result

    assert invoke('input', 'proof', tmp_path) is expected
    event = progress(tmp_path)
    assert event['stage'] == 'development_completed'
    assert event['trials_started'] == event['trials_completed'] == 1
    assert event['qualification_evidence'] is False
    output = capsys.readouterr().out
    assert 'secret_feature' not in output and 'secret_value' not in output
    assert 'accepted' not in event
    assert not (tmp_path / 'development_progress.tmp').exists()
    assert runtime._STATE.get() is None


def test_trial_is_inert_outside_development_scope(tmp_path, capsys):
    sentinel = object()
    trial = runtime.trace_trial(lambda *args: sentinel)
    assert trial(None, None, None, None) is sentinel
    assert capsys.readouterr().out == ''
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('exception', [ValueError('private-source-error'), KeyboardInterrupt(), SystemExit(3)])
def test_failure_preserves_exception_and_incomplete_trial_count(tmp_path, capsys, exception):
    @runtime.trace_trial
    def trial(*args):
        raise exception

    @runtime.trace_development_run
    def invoke(input_path, proof_path, output):
        return trial([], [], [], {})

    with pytest.raises(type(exception)) as caught:
        invoke('input', 'proof', tmp_path)
    assert caught.value is exception
    event = progress(tmp_path)
    assert event['stage'] == 'development_failed'
    assert event['trials_started'] == 1 and event['trials_completed'] == 0
    assert 'private-source-error' not in capsys.readouterr().out
    assert runtime._STATE.get() is None


def test_diagnostic_failure_cannot_replace_core_failure(tmp_path, monkeypatch):
    original = runtime._record
    failure = ValueError('original-computation-failure')
    def record(state, stage, **details):
        if stage.endswith('_failed'):
            raise OSError('diagnostic-disk-failure')
        return original(state, stage, **details)
    monkeypatch.setattr(runtime, '_record', record)

    @runtime.trace_development_run
    def invoke(input_path, proof_path, output):
        raise failure
    with pytest.raises(ValueError) as caught:
        invoke(None, None, tmp_path)
    assert caught.value is failure
    assert progress(tmp_path)['stage'] == 'development_started'
    assert runtime._STATE.get() is None


def test_initial_progress_failure_does_not_execute_or_leave_context(tmp_path, monkeypatch):
    calls = []
    def fail(*args, **kwargs):
        raise PermissionError('no-local-write')
    monkeypatch.setattr(runtime, '_record', fail)
    invoke = runtime.trace_development_run(lambda *args: calls.append(True))
    with pytest.raises(PermissionError):
        invoke(None, None, tmp_path)
    assert calls == [] and runtime._STATE.get() is None


def test_consecutive_runs_reset_counts_and_leave_rejections_unchanged(tmp_path):
    trial = runtime.trace_trial(lambda *args: {'accepted': False})
    invoke = runtime.trace_development_run(lambda *args: trial([], [], [], {}))
    for folder in (tmp_path / 'one', tmp_path / 'two'):
        assert invoke(None, None, folder) == {'accepted': False}
        assert progress(folder)['trials_started'] == progress(folder)['trials_completed'] == 1


def test_actual_runner_checksum_failure_retains_failure_not_acceptance(tmp_path):
    input_path = tmp_path / 'input.bin'; input_path.write_bytes(b'invalid')
    proof_path = tmp_path / 'proof.json'; proof_path.write_text(json.dumps({'input_table_sha256': '0' * 64}))
    output = tmp_path / 'output'
    with pytest.raises(ValueError, match='input table checksum mismatch'):
        run(input_path, proof_path, output)
    event = progress(output)
    assert event['stage'] == 'development_failed'
    assert event['trials_started'] == event['trials_completed'] == 0
    assert not (output / 'metrics.json').exists()


def test_synthetic_lightgbm_predictions_splits_and_metrics_are_exactly_preserved(tmp_path, monkeypatch):
    # Synthetic data only. Never open any retained qualification table in this test.
    rng = np.random.default_rng(1234)
    frame = pd.DataFrame(rng.normal(size=(280, 5)), columns=[f'x{i}' for i in range(5)])
    frame['home_win'] = (frame.x0 + .4 * frame.x1 > 0).astype(int)
    def derive(frame):
        return pd.DataFrame({f'd{i}': frame.x0 - (i + 1) * frame.x1 for i in range(120)}, index=frame.index)
    monkeypatch.setattr(subject, 'derive_frame', derive)
    selected = [f'd{i}' for i in range(120)]
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', pd.errors.PerformanceWarning)
        legacy = legacy_augment(frame, selected)
    revised = subject._augment(frame, selected)
    assert_frame_equal(revised, legacy, check_exact=True)
    columns = ['x0', 'x1', 'd0', 'd3', 'd119']
    params = {'objective': 'binary', 'n_estimators': 15, 'num_leaves': 4,
              'min_child_samples': 10, 'random_state': 1729, 'n_jobs': 2,
              'deterministic': True, 'force_col_wise': True, 'verbosity': -1}
    untouched = deepcopy(params)
    old_model = lgb.LGBMClassifier(**params).fit(legacy.iloc[:220][columns], legacy.iloc[:220].home_win)
    new_model = lgb.LGBMClassifier(**params).fit(revised.iloc[:220][columns], revised.iloc[:220].home_win)
    assert old_model.booster_.model_to_string() == new_model.booster_.model_to_string()
    np.testing.assert_array_equal(old_model.predict_proba(legacy.iloc[220:][columns]),
                                  new_model.predict_proba(revised.iloc[220:][columns]))
    plain = subject._trial(revised.iloc[:220], revised.iloc[220:], columns, params)
    invoke = runtime.trace_development_run(lambda *args: subject._trial(
        revised.iloc[:220], revised.iloc[220:], columns, params))
    traced = invoke(None, None, tmp_path)
    assert plain == traced and params == untouched
    assert progress(tmp_path)['trials_completed'] == 1
