from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path

import pytest
import yaml

from ks1 import nightly, platt, platt_inputs
from ks1.calibration_store import PREFIX, latest_checkpoint
from ks1.features import utc
from ks1.inventory import encode
from tests.ks1_calibration.test_calibration import rows
from tests.ks1_phase4.test_daily import MemoryS3

NOW = '2026-09-11T05:00:00+00:00'  # 01:00 EDT


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
    ('2026-09-11T04:59:00Z', None), ('2026-09-11T05:00:00Z', '2026-09-11'),
    ('2026-09-11T09:00:00Z', '2026-09-11'),  # delayed job catches up
    ('2026-12-11T05:59:00Z', None), ('2026-12-11T06:00:00Z', '2026-12-11'),
    ('2026-11-01T05:00:00Z', '2026-11-01'), ('2026-11-01T06:00:00Z', '2026-11-01'),
])
def test_0100_eastern_due_with_dst_and_delayed_runs(at, due):
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
    assert run(source(), tmp_path, store, committed)['status'] == 'not_due_or_already_completed'
    assert store.objects == before


def test_next_night_retains_grades_and_parameters_without_today_predictions(main_job, tmp_path, monkeypatch):
    store = Store()
    run(source(), tmp_path, store)
    old = checkpoint(store)
    tomorrow = '2026-09-12T05:00:00+00:00'
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
    captured = platt_inputs.capture(store, 'test', '2026-09-12T06:00:00Z', {'games': []}, [])
    assert captured['temperature_model'] == new['state']['temperature_model']
    assert captured['platt_model'] == new['state']['platt_model']
    assert len(captured['committed_ledger']['rows']) == 35


def test_minimum_30_and_corrections_do_not_rewrite_grades(main_job, tmp_path):
    store = Store()
    run(source(29), tmp_path, store)
    old = checkpoint(store)
    assert old['state']['temperature_model']['T'] == 1 and old['state']['temperature_model']['n'] == 0
    changed = source(35, '2026-09-12T05:00:00+00:00')
    changed['finals']['0']['home_score'] = 9
    before = deepcopy(store.objects)
    with pytest.raises(ValueError, match='observation changed'):
        run(changed, tmp_path, store, old)
    assert store.objects == before
    run(source(35, '2026-09-12T05:00:00+00:00'), tmp_path, store, old)
    new = checkpoint(store, '2026-09-12T05:00:00Z')
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


def test_existing_workflow_runs_nightly_before_ingestion_and_publish_without_extra_scheduler():
    path = Path(__file__).resolve().parents[2]/'.github/workflows/mlb-research-ingestion.yml'
    workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
    assert workflow['on']['schedule'] == [{'cron': '17 * * * *'}]
    steps = workflow['jobs']['ingest']['steps']
    commands = [s['run'] for s in steps if 'run' in s]
    night = next(i for i, s in enumerate(commands) if 'ks1.nightly' in s)
    ingest = next(i for i, s in enumerate(commands) if 'run_mlb_research_ingestion.py' in s)
    publish = next(i for i, s in enumerate(commands) if 'ks1.daily' in s)
    assert night < ingest < publish
    assert 'refs/heads/main' in workflow['jobs']['ingest']['if']
    assert 'pull_request' in workflow['jobs']['verify-ks1']['if']
