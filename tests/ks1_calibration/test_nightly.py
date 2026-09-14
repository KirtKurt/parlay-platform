from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path

import pytest
import yaml

from ks1 import nightly, platt, platt_inputs
from ks1.calibration_store import PREFIX, checkpoint_prefix, latest_checkpoint
from ks1.features import utc
from ks1.inventory import encode
from tests.ks1_calibration.test_calibration import rows
from tests.ks1_phase4.test_daily import MemoryS3

NOW = '2026-09-11T07:00:00+00:00'  # 03:00 EDT


class Store(MemoryS3):
    def get_paginator(self, operation):
        assert operation == 'list_objects_v2'
        store = self
        class Pages:
            def paginate(self, Bucket, Prefix):
                return [{'Contents': [{'Key': key} for key in store.objects if key.startswith(Prefix)]}]
        return Pages()


@pytest.fixture
def main_job(monkeypatch):
    for k, v in {'GITHUB_ACTIONS': 'true', 'GITHUB_REPOSITORY': 'KirtKurt/parlay-platform',
                 'GITHUB_REF': 'refs/heads/main', 'GITHUB_EVENT_NAME': 'schedule',
                 'GITHUB_WORKFLOW_REF': 'KirtKurt/parlay-platform/.github/workflows/mlb-research-ingestion.yml@refs/heads/main'}.items():
        monkeypatch.setenv(k, v)


def source(n=35, at=NOW):
    result = {'system': 'KS1', 'as_of': at, 'locked': [], 'finals': {},
              'final_sources': [{'sha256': 'a'*64}], 'platt_model': platt.identity(),
              'temperature_model': platt.temperature_identity()}
    for row in rows(n):
        pk = row['game_id']
        game = {'game_id': pk, 'model_version': row['raw_model_version'], 'as_of': row['as_of'],
                'date': row['as_of'][:10], 'home_id': '1', 'away_id': '2',
                'commence_time': (utc(row['locked_at'])+timedelta(minutes=10)).isoformat(),
                'p_raw': row['p_raw'], 'p_home': .81 if row['p_raw'] > .5 else .19,
                'calibration_version': 'synthetic-test-mapping', 'official_probability_field': 'p_home'}
        result['locked'].append({'row': game, 'evidence': {'version_id': 'test-original-'+pk,
                                  'stored_at': row['as_of'], 'sha256': 'a'*64}})
        result['finals'][pk] = {'home_id': '1', 'away_id': '2', 'home_score': 5 if row['home_win'] else 3,
                                'away_score': 4, 'completed_at': row['graded_at'], 'observed_at': row['graded_at']}
    return result


def run(source, output, store, checkpoint=None):
    return nightly.execute(source, output, s3=store, bucket='test', checkpoint=checkpoint,
                           clock=lambda: utc(source['as_of'])+timedelta(seconds=30))


def checkpoint(store, at=NOW):
    return latest_checkpoint(store, 'test', (utc(at)+timedelta(minutes=1)).isoformat())


@pytest.mark.parametrize('at,due', [
    ('2026-09-11T06:59:00Z', None), ('2026-09-11T07:00:00Z', '2026-09-11'),
    ('2026-09-11T11:00:00Z', '2026-09-11'),  # delayed job catches up
    ('2026-12-11T07:59:00Z', None), ('2026-12-11T08:00:00Z', '2026-12-11'),
    ('2026-11-01T05:00:00Z', None), ('2026-11-01T06:00:00Z', None),
    ('2026-11-01T07:59:59Z', None), ('2026-11-01T08:00:00Z', '2026-11-01'),
    ('2026-03-08T06:59:59Z', None), ('2026-03-08T07:00:00Z', '2026-03-08'),
    ('2026-09-11T05:00:00Z', None), ('2026-09-11T06:00:00Z', None),
])
def test_0300_eastern_due_with_dst_and_delayed_runs(at, due):
    assert nightly.due_date(at) == due
    if due:
        assert nightly.due_date(at, {'state': {'night_date': due}}) is None


def test_commit_and_readback_precede_fit_and_official_grades_use_locked_number(main_job, tmp_path, monkeypatch):
    store = Store()
    prediction = PREFIX+'date=2026-09-10/predictions.parquet'
    store.objects[prediction] = b'immutable-original-predictions'
    inputs = source()
    original = deepcopy(inputs)
    fit = nightly.fit_from_ledger
    def after_commit(data, **kwargs):
        assert store.writes == [PREFIX+'date=2026-09-11/graded_ledger.json']
        saved = json.loads(store.objects[store.writes[0]])
        assert saved['rows'] == data
        assert saved['rows'][0]['p_home'] == .19 and saved['rows'][0]['p_raw'] == .05
        assert kwargs['as_of'] > inputs['as_of']
        return fit(data, **kwargs)
    monkeypatch.setattr(nightly, 'fit_from_ledger', after_commit)
    report = run(inputs, tmp_path, store)
    committed = checkpoint(store)
    model = committed['state']['temperature_model']
    assert report['ledger_rows'] == 35 and report['prediction_writes'] == 0
    assert model['T'] > 1 and model['n'] == 35
    assert report['official_metrics'] == platt.metrics(
        [r['home_win'] for r in committed['ledger']['rows']], [r['p_home'] for r in committed['ledger']['rows']])
    assert store.objects[prediction] == b'immutable-original-predictions' and inputs == original
    assert all(key.startswith(PREFIX+'date=2026-09-11/') for key in store.writes)


@pytest.mark.parametrize('failure', ['write', 'readback'])
def test_failed_ledger_write_or_readback_never_calls_calibrator(main_job, tmp_path, monkeypatch, failure):
    class Broken(Store):
        def put_object(self, **kwargs):
            if failure == 'write':
                raise RuntimeError('ledger-write-failed')
            result = super().put_object(**kwargs)
            self.objects[kwargs['Key']] = b'{}'
            return result
    store = Broken()
    monkeypatch.setattr(nightly, 'fit_from_ledger', lambda *a, **k: pytest.fail('fit ran before verified ledger write'))
    with pytest.raises((RuntimeError, ValueError), match='ledger-write-failed|readback mismatch'):
        run(source(), tmp_path, store)
    assert not (tmp_path/'data/models/temperature.json').exists()
    assert not any(k.endswith('calibration_state.json') for k in store.objects)


def test_state_write_failure_retries_committed_ledger_without_double_shrink(main_job, tmp_path):
    class FailOnce(Store):
        fail = True
        def put_object(self, **kwargs):
            if kwargs['Key'].endswith('calibration_state.json') and self.fail:
                self.fail = False
                raise RuntimeError('state-write-failed')
            return super().put_object(**kwargs)
    store = FailOnce()
    with pytest.raises(RuntimeError, match='state-write-failed'):
        run(source(), tmp_path, store)
    first_t = json.loads((tmp_path/'data/models/temperature.json').read_bytes())['T']
    report = run(source(), tmp_path, store)
    committed = checkpoint(store)
    assert committed['state']['temperature_model']['T'] == first_t > 1
    assert len(store.writes) == 2 and report['new_grades'] == 35
    before = deepcopy(store.objects)
    assert run(source(), tmp_path, store, committed)['status'] == 'no_new_final_grades'
    assert store.objects == before


def test_next_night_retains_grades_and_parameters_without_today_predictions(main_job, tmp_path, monkeypatch):
    store = Store()
    run(source(), tmp_path, store)
    old = checkpoint(store)
    tomorrow = '2026-09-12T07:00:00+00:00'
    report = run(source(0, tomorrow), tmp_path, store, old)
    new = checkpoint(store, tomorrow)
    assert new['ledger']['rows'] == old['ledger']['rows']
    assert new['state']['temperature_model'] == old['state']['temperature_model']
    assert report['new_grades'] == 0 and report['ledger_rows'] == 35
    assert report['temperature_decision']['status'] == 'unchanged_ledger_no_refit'
    # A stale hourly model copy must not reset the accepted nightly mapping.
    store.objects[PREFIX+'date=2026-09-12/temperature.json'] = encode(platt.temperature_identity())
    store.objects[PREFIX+'date=2026-09-12/platt.json'] = encode(platt.identity())
    monkeypatch.setattr(platt_inputs, 'read_locked_predictions', lambda *a: ([], {}))
    captured = platt_inputs.capture(store, 'test', '2026-09-12T08:00:00Z', {'games': []}, [])
    assert captured['temperature_model'] == new['state']['temperature_model']
    assert captured['platt_model'] == new['state']['platt_model']
    assert len(captured['committed_ledger']['rows']) == 35


def test_minimum_30_and_corrections_do_not_rewrite_grades(main_job, tmp_path):
    store = Store()
    run(source(29), tmp_path, store)
    old = checkpoint(store)
    assert old['state']['temperature_model']['T'] == 1 and old['state']['temperature_model']['n'] == 0
    changed = source(35, '2026-09-12T07:00:00+00:00')
    changed['finals']['0']['home_score'] = 9
    before = deepcopy(store.objects)
    with pytest.raises(ValueError, match='observation changed'):
        run(changed, tmp_path, store, old)
    assert store.objects == before
    run(source(35, '2026-09-12T07:00:00+00:00'), tmp_path, store, old)
    new = checkpoint(store, '2026-09-12T07:00:00Z')
    assert new['state']['temperature_model']['n'] == 35 and new['state']['temperature_model']['T'] > 1
    assert new['ledger']['rows'][:29] == old['ledger']['rows']


def test_branch_and_verification_inputs_cannot_publish(main_job, tmp_path, monkeypatch):
    store = Store()
    monkeypatch.setenv('GITHUB_REF', 'refs/pull/705/merge')
    with pytest.raises(ValueError, match='existing main'):
        run(source(), tmp_path, store)
    monkeypatch.setenv('GITHUB_REF', 'refs/heads/main')
    with pytest.raises(ValueError, match='verification'):
        run(dict(source(), verification_only=True), tmp_path, store)
    assert not store.writes


def test_missing_or_corrupt_committed_ledger_fails_closed(main_job, tmp_path):
    store = Store()
    run(source(), tmp_path, store)
    store.objects[PREFIX+'date=2026-09-11/graded_ledger.json'] = b'{}'
    with pytest.raises(ValueError, match='missing or changed'):
        checkpoint(store)


def test_workflow_refreshes_finals_before_nightly_and_does_not_block_daily_on_grading_failure():
    path = Path(__file__).resolve().parents[2]/'.github/workflows/mlb-research-ingestion.yml'
    workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
    assert workflow['on']['schedule'] == [{'cron': '17 * * * *'}]
    steps = workflow['jobs']['ingest']['steps']
    commands = [s['run'] for s in steps if 'run' in s]
    night = next(i for i, s in enumerate(commands) if 'ks1.nightly' in s)
    ingest = next(i for i, s in enumerate(commands) if 'run_mlb_research_ingestion.py' in s)
    publish = next(i for i, s in enumerate(commands) if 'ks1.daily' in s)
    assert ingest < night < publish
    refresh = next(s for s in steps if s.get('id') == 'research')
    assert 'started_at=' in refresh['run'] and 'GITHUB_OUTPUT' in refresh['run']
    nightly_step = next(s for s in steps if s.get('id') == 'nightly')
    assert '--sources-not-before' in nightly_step['run']
    assert nightly_step['env']['SOURCES_NOT_BEFORE'] == '${{ steps.research.outputs.started_at }}'
    assert 'continue-on-error' not in nightly_step
    daily = next(s for s in steps if 'ks1.daily' in s.get('run', ''))
    assert daily['if'] == "${{ !cancelled() && steps.research.outcome == 'success' }}"
    assert 'refs/heads/main' in workflow['jobs']['ingest']['if']
    assert 'pull_request' in workflow['jobs']['verify-ks1']['if']


def fresh_prior():
    return {'coverageComplete': True, 'games': [],
            'receipt': {'retrievedAtUtc': '2026-09-11T07:00:10Z'},
            'updatedAtUtc': '2026-09-11T07:00:30Z'}


def test_fresh_complete_results_allow_an_empty_real_slate():
    nightly.require_fresh_finals(fresh_prior(), NOW, '2026-09-11T07:01:00Z')


@pytest.mark.parametrize('change', [
    {'coverageComplete': False}, {'coverageComplete': 'true'},
    {'updatedAtUtc': '2026-09-11T06:59:00Z'},  # LEASE_BUSY/stale cache
    {'receipt': {'retrievedAtUtc': '2026-09-10T01:00:00Z'}},
    {'receipt': {}}, {'updatedAtUtc': None},
    {'updatedAtUtc': '2026-09-11T07:02:00Z'},  # future source
])
def test_stale_incomplete_missing_or_future_results_cannot_seal_the_night(change):
    with pytest.raises(ValueError, match='incomplete|stale|future'):
        nightly.require_fresh_finals(dict(fresh_prior(), **change), NOW, '2026-09-11T07:01:00Z')


def test_due_nightly_cli_fails_before_capture_or_writes_on_stale_finals(main_job, tmp_path, monkeypatch):
    import sys
    from datetime import datetime
    from ks1 import sources

    class Clock:
        @staticmethod
        def now(tz):
            return datetime.fromisoformat('2026-09-11T07:01:00+00:00')

    prior = fresh_prior()
    prior['receipt']['retrievedAtUtc'] = '2026-09-10T01:00:00Z'
    class ReadOnly:
        def __init__(self, *args):
            self.receipts = []
        def read(self, key):
            return {'artifact': {}}
        def pointer(self, pointer):
            return prior

    monkeypatch.setattr(nightly, 'datetime', Clock)
    monkeypatch.setattr(nightly, 'Reader', ReadOnly)
    monkeypatch.setattr(sources, 'aws_clients', lambda *a: (None, object(), 'test'))
    monkeypatch.setattr(nightly, 'latest_checkpoint', lambda *a: None)
    monkeypatch.setattr(nightly, 'capture', lambda *a: pytest.fail('stale finals reached capture'))
    monkeypatch.setattr(nightly, 'execute', lambda *a, **kw: pytest.fail('stale finals reached ledger writes'))
    monkeypatch.setattr(sys, 'argv', ['nightly', '--publish', '--sources-not-before', NOW,
                                    '--output', str(tmp_path)])
    with pytest.raises(ValueError, match='stale'):
        nightly.main()
    report = json.loads((tmp_path/'report.json').read_text())
    assert report['status'] == 'source_refresh_not_verified'
    assert report['ledger_writes'] == 0 and report['published'] is False


def test_publish_requires_source_refresh_start():
    with pytest.raises(ValueError, match='requires the source refresh start'):
        nightly.require_fresh_finals(fresh_prior(), None, '2026-09-11T07:01:00Z')


def test_late_finals_append_once_preserve_originals_and_never_refit(main_job, tmp_path, monkeypatch):
    store = Store()
    # Reproduce the incident: four retained locks, but only two finals when the
    # first nightly checkpoint completes. The other two labels arrive later.
    partial = source(4)
    del partial['finals']['2'], partial['finals']['3']
    run(partial, tmp_path, store)
    old = checkpoint(store)
    assert len(old['ledger']['rows']) == 2
    originals = deepcopy(store.objects)
    monkeypatch.setattr(nightly, 'fit_from_ledger', lambda *a, **k: pytest.fail('catch-up fitted temperature'))
    monkeypatch.setattr(nightly, 'refit', lambda *a, **k: pytest.fail('catch-up fitted Platt'))
    pending = run(dict(partial, as_of='2026-09-11T07:30:00Z'), tmp_path, store, old)
    assert pending['status'] == 'no_new_final_grades'
    assert len(pending['admission']['excluded']) == 2 and store.objects == originals
    at = '2026-09-11T08:00:00Z'
    report = run(source(4, at), tmp_path, store, old)
    new = checkpoint(store, at)
    assert report['status'] == 'completed_catchup' and report['new_grades'] == 2
    assert report['ledger_rows'] == 4 and report['ledger_readback_verified'] is True
    assert report['prediction_writes'] == 0 and report['calibration_fitted'] is False
    assert new['ledger']['rows'][:2] == old['ledger']['rows']
    for kind in ('temperature_model', 'platt_model'):
        assert new['state'][kind] == old['state'][kind]
    assert all(store.objects[k] == value for k, value in originals.items())
    prefix = checkpoint_prefix('2026-09-11', 1)
    assert report['write_keys'] == [prefix+'graded_ledger.json', prefix+'calibration_state.json']
    assert report['official_metrics'] == platt.metrics(
        [r['home_win'] for r in new['ledger']['rows']], [r['p_home'] for r in new['ledger']['rows']])
    before = deepcopy(store.objects)
    writes = list(store.writes)
    repeat = run(source(4, '2026-09-11T09:00:00Z'), tmp_path, store, new)
    assert repeat['status'] == 'no_new_final_grades' and repeat['published'] is False
    assert repeat['new_grades'] == 0 and repeat['write_keys'] == []
    assert store.objects == before and store.writes == writes


def test_crossing_30_in_catchup_waits_until_next_night_to_fit(main_job, tmp_path):
    store = Store()
    run(source(29), tmp_path, store)
    old = checkpoint(store)
    run(source(35, '2026-09-11T08:00:00Z'), tmp_path, store, old)
    caught_up = checkpoint(store, '2026-09-11T08:00:00Z')
    assert caught_up['state']['temperature_model'] == old['state']['temperature_model']
    assert caught_up['state']['temperature_model']['n'] == 0
    tomorrow = '2026-09-12T07:00:00Z'
    run(source(0, tomorrow), tmp_path, store, caught_up)
    fitted = checkpoint(store, tomorrow)
    assert fitted['state']['temperature_model']['n'] == 35
    assert fitted['state']['temperature_model']['T'] > 1
    assert fitted['ledger']['rows'] == caught_up['ledger']['rows']
    assert fitted['state'].get('catchup_revision', 0) == 0


def test_interrupted_catchup_resumes_immutable_ledger_then_catches_later_finals(main_job, tmp_path):
    class FailOnce(Store):
        fail = True
        def put_object(self, **kwargs):
            if 'catchup=' in kwargs['Key'] and kwargs['Key'].endswith('calibration_state.json') and self.fail:
                self.fail = False
                raise RuntimeError('catchup-state-write-failed')
            return super().put_object(**kwargs)
    store = FailOnce()
    run(source(2), tmp_path, store)
    old = checkpoint(store)
    with pytest.raises(RuntimeError, match='catchup-state-write-failed'):
        run(source(4, '2026-09-11T08:00:00Z'), tmp_path, store, old)
    assert checkpoint(store, '2026-09-11T08:00:00Z') == old
    saved_key = checkpoint_prefix('2026-09-11', 1)+'graded_ledger.json'
    saved_bytes = store.objects[saved_key]
    # Six finals now exist, but finish the original four-row transaction first.
    report = run(source(6, '2026-09-11T09:00:00Z'), tmp_path, store, old)
    assert report['ledger_rows'] == 4 and report['new_grades'] == 2
    assert store.objects[saved_key] == saved_bytes
    first = checkpoint(store, '2026-09-11T09:00:00Z')
    report = run(source(6, '2026-09-11T10:00:00Z'), tmp_path, store, first)
    assert report['ledger_rows'] == 6 and report['catchup_revision'] == 2
    latest = checkpoint(store, '2026-09-11T10:00:00Z')
    assert latest['ledger']['rows'][:4] == first['ledger']['rows']
    assert latest['state']['temperature_model'] == old['state']['temperature_model']
    assert len(store.writes) == 6


@pytest.mark.parametrize('change', ['p_home', 'outcome', 'missing_lock', 'future_final'])
def test_catchup_rejects_corrections_and_excludes_unverified_new_grades(main_job, tmp_path, change):
    store = Store()
    run(source(2), tmp_path, store)
    old = checkpoint(store)
    inputs = source(3, '2026-09-11T08:00:00Z')
    before = deepcopy(store.objects)
    if change == 'p_home':
        inputs['locked'][0]['row']['p_home'] = .2
    elif change == 'outcome':
        inputs['finals']['0']['home_score'] = 9
    elif change == 'missing_lock':
        inputs['locked'][2]['evidence']['version_id'] = None
    else:
        inputs['finals']['2']['observed_at'] = '2026-09-11T09:00:00Z'
    if change == 'future_final':
        report = run(inputs, tmp_path, store, old)
        assert report['status'] == 'no_new_final_grades'
    else:
        with pytest.raises(ValueError, match='rewrite|observation changed|lock evidence'):
            run(inputs, tmp_path, store, old)
    assert store.objects == before


def test_catchup_readback_failure_never_exposes_new_checkpoint(main_job, tmp_path):
    class Broken(Store):
        def put_object(self, **kwargs):
            result = super().put_object(**kwargs)
            if 'catchup=' in kwargs['Key']:
                self.objects[kwargs['Key']] = b'{}'
            return result
    store = Broken()
    run(source(2), tmp_path, store)
    old = checkpoint(store)
    with pytest.raises(ValueError, match='readback mismatch'):
        run(source(4, '2026-09-11T08:00:00Z'), tmp_path, store, old)
    assert checkpoint(store, '2026-09-11T08:00:00Z') == old
    assert not any('catchup=' in k and k.endswith('calibration_state.json') for k in store.objects)


@pytest.mark.parametrize('damage', ['ledger', 'escaped_pointer', 'revision', 'future'])
def test_latest_catchup_checkpoint_fails_closed(main_job, tmp_path, damage):
    store = Store()
    run(source(2), tmp_path, store)
    run(source(4, '2026-09-11T08:00:00Z'), tmp_path, store, checkpoint(store))
    prefix = checkpoint_prefix('2026-09-11', 1)
    key = prefix+'calibration_state.json'
    state = json.loads(store.objects[key])
    if damage == 'ledger':
        store.objects[prefix+'graded_ledger.json'] = b'{}'
    elif damage == 'escaped_pointer':
        state['ledger']['key'] = PREFIX+'date=2026-09-11/graded_ledger.json'
    elif damage == 'revision':
        state['catchup_revision'] = 2
    else:
        state['completed_at'] = '2026-09-11T10:00:00Z'
    store.objects[key] = encode(state)
    with pytest.raises(ValueError, match='missing or changed|escaped|invalid or future'):
        checkpoint(store, '2026-09-11T08:00:00Z')


def test_daily_capture_reads_catchup_ledger_but_keeps_nightly_models(main_job, tmp_path, monkeypatch):
    store = Store()
    run(source(2), tmp_path, store)
    old = checkpoint(store)
    run(source(4, '2026-09-11T08:00:00Z'), tmp_path, store, old)
    monkeypatch.setattr(platt_inputs, 'read_locked_predictions', lambda *a: ([], {}))
    captured = platt_inputs.capture(store, 'test', '2026-09-11T09:00:00Z', {'games': []}, [])
    assert len(captured['committed_ledger']['rows']) == 4
    assert captured['temperature_model'] == old['state']['temperature_model']
    assert captured['platt_model'] == old['state']['platt_model']


@pytest.mark.parametrize('stale', [False, True])
def test_completed_nightly_cli_still_checks_fresh_finals(main_job, tmp_path, monkeypatch, stale):
    import sys
    from ks1 import sources
    store = Store()
    run(source(2), tmp_path, store)
    old = checkpoint(store)
    at = '2026-09-11T08:01:00+00:00'
    class Clock:
        @staticmethod
        def now(tz):
            return utc(at)
    prior = {'coverageComplete': True, 'games': [],
             'receipt': {'retrievedAtUtc': '2026-09-11T07:00:10Z' if stale else '2026-09-11T08:00:10Z'},
             'updatedAtUtc': '2026-09-11T08:00:30Z'}
    class ReadOnly:
        def __init__(self, *args): self.receipts = []
        def read(self, key): return {'artifact': {}}
        def pointer(self, pointer): return prior
    calls = []
    def captured(*args):
        calls.append('captured')
        return source(4, at)
    monkeypatch.setattr(nightly, 'datetime', Clock)
    monkeypatch.setattr(nightly, 'Reader', ReadOnly)
    monkeypatch.setattr(sources, 'aws_clients', lambda *a: (None, store, 'test'))
    monkeypatch.setattr(nightly, 'capture', captured)
    monkeypatch.setattr(sys, 'argv', ['nightly', '--publish', '--sources-not-before', '2026-09-11T08:00:00Z',
                                    '--output', str(tmp_path)])
    if stale:
        with pytest.raises(ValueError, match='stale'):
            nightly.main()
        assert not calls and checkpoint(store, at) == old
    else:
        nightly.main()
        assert calls == ['captured']
        assert len(checkpoint(store, at)['ledger']['rows']) == 4
        assert json.loads((tmp_path/'report.json').read_text())['status'] == 'completed_catchup'


def test_catchup_keeps_main_only_guard_and_pre_0300_gate(main_job, tmp_path, monkeypatch):
    store = Store()
    run(source(2), tmp_path, store)
    old = checkpoint(store)
    original = deepcopy(store.objects)
    monkeypatch.setenv('GITHUB_REF', 'refs/pull/999/merge')
    with pytest.raises(ValueError, match='existing main'):
        run(source(4, '2026-09-11T08:00:00Z'), tmp_path, store, old)
    monkeypatch.setenv('GITHUB_REF', 'refs/heads/main')
    with pytest.raises(ValueError, match='verification'):
        run(dict(source(4, '2026-09-11T08:00:00Z'), verification_only=True), tmp_path, store, old)
    result = run(source(4, '2026-09-12T06:59:00Z'), tmp_path, store, old)
    assert result['published'] is False and store.objects == original


def test_local_catchup_is_preview_only(main_job, tmp_path):
    store = Store()
    run(source(2), tmp_path, store)
    old = checkpoint(store)
    original = deepcopy(store.objects)
    inputs = source(4, '2026-09-11T08:00:00Z')
    report = nightly.execute(inputs, tmp_path/'preview', checkpoint=old,
                             clock=lambda: utc(inputs['as_of'])+timedelta(seconds=30))
    assert report['status'] == 'completed_catchup' and report['published'] is False
    assert report['ledger_readback_verified'] is False and report['write_keys'] == []
    assert store.objects == original
