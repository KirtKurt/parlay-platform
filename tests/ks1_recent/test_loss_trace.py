from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import subprocess
from textwrap import dedent

import pytest
from botocore.exceptions import ClientError

import ks1.loss_trace as subject


def _source():
    rows = []
    for gid, p_home in [('1', .60), ('2', .55), ('3', .45)]:
        evidence = {
            'bucket': 'b', 'key': f'k/{gid}', 'version_id': f'v{gid}',
            'sha256': gid * 64, 'stored_at': '2026-09-18T19:50:00+00:00'
        }
        rows.append({'evidence': evidence, 'row': {
            'game_id': gid, 'date': '2026-09-18', 'model_version': 'M', 'p_home': p_home,
            'home_id': '10', 'away_id': '20',
            'starter_profile_json': '{}', 'lineup_bullpen_profile_json': '{}',
            'signal_contributions_json': json.dumps({'groups': {
                'starter': {'decision_influence_pct': 70 + int(gid), 'signal_score': .1 * int(gid)}
            }}),
            '_flags': [{'code': 'A' if gid != '2' else 'B', 'strength': 'medium'}],
        }})
    wins = {'1': 1, '2': 0, '3': 1}
    finals = {}
    for gid in wins:
        home_score, away_score = ((2, 1) if wins[gid] else (1, 2))
        finals[gid] = {
            'home_score': home_score, 'away_score': away_score,
            'completed_at': '2026-09-18T23:00:00+00:00',
        }
    return {
        'system': 'KS1', 'as_of': '2026-09-19T05:00:00+00:00',
        'locked': rows, 'finals': finals,
        'final_sources': [{'sha256': 'f' * 64}],
    }


def _ledger(source):
    rows = []
    wins = {'1': 1, '2': 0, '3': 1}
    for entry in source['locked']:
        row = entry['row']
        gid = row['game_id']
        final = source['finals'][gid]
        rows.append({
            'game_id': gid, 'signature': f's{gid}', 'home_win': wins[gid],
            'home_score': final['home_score'], 'away_score': final['away_score'],
            'final_evidence': source['final_sources'],
            'locked_at': '2026-09-18T19:50:00+00:00', 'raw_model_version': 'M',
            'p_home': row['p_home'], 'lock_evidence': entry['evidence'],
        })
    return {'system': 'KS1', 'as_of': source['as_of'], 'rows': rows}


def _verified(source, grades):
    locked = {entry['row']['game_id']: entry['row'] for entry in source['locked']}
    return {
        str(grade['game_id']): {
            'home_score': grade['home_score'],
            'away_score': grade['away_score'],
            'home_id': str(locked[str(grade['game_id'])]['home_id']),
            'away_id': str(locked[str(grade['game_id'])]['away_id']),
            'final_evidence': grade['final_evidence'],
        }
        for grade in grades
    }


def _build(source, ledger, frozen):
    return subject.build(
        source, ledger, frozen,
        lambda grades: _verified(source, grades),
    )


def _patch(monkeypatch, source):
    admitted = []
    wins = {'1': 1, '2': 0, '3': 1}
    for entry in source['locked']:
        gid = entry['row']['game_id']
        admitted.append({'game_id': gid, 'signature': f's{gid}', 'home_win': wins[gid]})
    def admitted_subset(source, include_predecessors):
        ids = {entry['row']['game_id'] for entry in source['locked']}
        rows = [row for row in admitted if row['game_id'] in ids]
        return rows, {'eligible_graded_rows': len(rows)}
    monkeypatch.setattr(subject, 'dataset', admitted_subset)
    monkeypatch.setattr(subject, 'evaluate', lambda row: {
        'selected_team': 'H' if row['p_home'] >= .5 else 'A',
        'model_selected_probability': max(row['p_home'], 1 - row['p_home']),
        'severity': 'watch', 'counter_signal_points': 1, 'flags': row['_flags'],
    })


def test_verified_final_receipts_reads_the_grade_version(monkeypatch):
    grade = {
        'game_id': '1', 'home_score': 2, 'away_score': 1,
        'final_evidence': [{
            'bucket': 'b', 'key': 'prior.json', 'versionId': 'v1',
            'sha256': 'a' * 64,
        }],
    }
    payload = {'games': [{
        'officialGamePk': 1, 'completedAtUtc': '2026-09-18T23:00:00+00:00',
        'teams': {
            'home': {'team': {'id': 10}, 'teamStats': {'batting': {'runs': 2}}},
            'away': {'team': {'id': 20}, 'teamStats': {'batting': {'runs': 1}}},
        },
    }]}
    seen = {}
    def read_json(s3, bucket, key, version):
        seen['args'] = (bucket, key, version)
        return payload, {'sha256': 'a' * 64}
    monkeypatch.setattr(subject, 'read_json', read_json)
    out = subject.verified_final_receipts(object(), 'b', [grade])
    assert seen['args'] == ('b', 'prior.json', 'v1')
    assert out['1']['home_id'] == '10' and out['1']['away_id'] == '20'
    grade['home_score'] = 9
    with pytest.raises(ValueError, match='score differs'):
        subject.verified_final_receipts(object(), 'b', [grade])


def test_build_excludes_frozen_holdout_and_summarizes_patterns(monkeypatch):
    source = _source()
    ledger = _ledger(source)
    _patch(monkeypatch, source)
    out = _build(source, ledger, {'3'})
    assert out['sample']['committed_ledger_rows'] == 3
    assert out['sample']['frozen_holdout_ids_excluded'] == 1
    assert out['sample']['analyzed_non_holdout_rows'] == 2
    assert out['holdout_boundary']['holdout_labels_read'] == 0
    assert out['holdout_boundary']['holdout_predictions_scored'] == 0
    assert out['summary_scope'] == 'per_raw_model_version_only'
    assert [summary['raw_model_version'] for summary in out['model_summaries']] == ['M']
    summary = out['model_summaries'][0]
    assert {row['pattern'] for row in summary['flag_summary']} == {'A', 'B'}
    starter = next(row for row in summary['contribution_summary'] if row['group'] == 'starter')
    assert starter['win_rows'] == 1 and starter['loss_rows'] == 1
    assert out['prediction_writes'] == out['official_ledger_writes'] == 0
    assert out['model_ref_writes'] == out['lock_writes'] == 0
    assert out['trained_lightgbm'] is False


def test_selected_side_score_orientation_and_model_stratification(monkeypatch):
    source = _source()
    ledger = _ledger(source)
    source['locked'][2]['row']['model_version'] = 'M2'
    ledger['rows'][2]['raw_model_version'] = 'M2'
    _patch(monkeypatch, source)
    out = _build(source, ledger, set())
    assert [row['raw_model_version'] for row in out['model_summaries']] == ['M', 'M2']
    observations = {row['game_id']: row for row in out['observations']}
    assert observations['1']['contribution_groups']['starter']['signal_score'] == pytest.approx(.1)
    assert observations['3']['contribution_groups']['starter']['signal_score'] == pytest.approx(-.3)


def test_build_rejects_lock_evidence_drift(monkeypatch):
    source = _source()
    ledger = _ledger(source)
    _patch(monkeypatch, source)
    source['locked'][0]['evidence'] = dict(source['locked'][0]['evidence'], version_id='advanced')
    with pytest.raises(ValueError, match='committed lock evidence'):
        _build(source, ledger, set())


def test_holdout_id_contract_is_exactly_300_unique(monkeypatch, tmp_path):
    path = tmp_path / 'holdout.json'
    ids = [str(i) for i in range(300)]
    monkeypatch.setattr(subject, 'FROZEN_GAME_IDS_SHA256', hashlib.sha256(subject.encode(ids)).hexdigest())
    path.write_text(json.dumps({'game_ids': ids}))
    assert len(subject.holdout_ids(path)) == 300
    path.write_text(json.dumps({'game_ids': ['x'] * 300}))
    with pytest.raises(ValueError, match='300 unique'):
        subject.holdout_ids(path)
    path.write_text(json.dumps({'game_ids': list(reversed(ids))}))
    with pytest.raises(ValueError, match='game identities changed'):
        subject.holdout_ids(path)


def test_publish_uses_latest_committed_checkpoint_and_readback(monkeypatch, tmp_path):
    source = _source()
    ledger = _ledger(source)
    monkeypatch.setattr(subject, 'require_main_workflow', lambda: None)
    monkeypatch.setattr(subject, 'latest_checkpoint', lambda s3, bucket, as_of: {
        'ledger': ledger, 'state': {'night_date': '2026-09-19', 'catchup_revision': 2}
    })
    monkeypatch.setattr(subject, 'holdout_ids', lambda: set())
    monkeypatch.setattr(subject, 'read_json', lambda s3, bucket, key: (None, None))
    monkeypatch.setattr(subject, 'build', lambda source, ledger, frozen_ids, verify_finals: {
        'sample': {'analyzed_non_holdout_rows': 3}, 'authority_effect': 'none'
    })
    seen = {}

    def commit(s3, bucket, key, payload):
        seen['key'] = key
        return payload, {'key': key, 'sha256': 'a' * 64}

    monkeypatch.setattr(subject, 'commit_json', commit)
    out = subject.publish(source, tmp_path / 'loss_trace.json', s3=object(), bucket='bucket')
    assert seen['key'].endswith('date=2026-09-19/catchup=000002/loss_trace.json')
    assert out['status'] == 'published'
    assert json.loads((tmp_path / 'loss_trace.json').read_text())['sample']['analyzed_non_holdout_rows'] == 3


def test_publish_reuses_verified_existing_checkpoint_trace(monkeypatch, tmp_path):
    source = _source()
    ledger = _ledger(source)
    existing = {
        'contract': subject.CONTRACT, 'system': 'KS1', 'as_of': source['as_of'],
        'ledger_as_of': ledger['as_of'],
        'sample': {'analyzed_non_holdout_rows': 3}, 'authority_effect': 'none',
        'prediction_writes': 0, 'official_ledger_writes': 0,
        'model_ref_writes': 0, 'lock_writes': 0,
    }
    proof = {'key': 'existing', 'sha256': 'a' * 64}
    monkeypatch.setattr(subject, 'require_main_workflow', lambda: None)
    monkeypatch.setattr(subject, 'latest_checkpoint', lambda *args: {
        'ledger': ledger, 'state': {'night_date': '2026-09-19', 'catchup_revision': 2}
    })
    monkeypatch.setattr(subject, 'read_json', lambda *args: (existing, proof))
    monkeypatch.setattr(subject, 'holdout_ids', lambda: pytest.fail('read holdout for existing trace'))
    monkeypatch.setattr(subject, 'build', lambda *args: pytest.fail('rebuilt existing trace'))
    monkeypatch.setattr(subject, 'commit_json', lambda *args: pytest.fail('rewrote existing trace'))
    output = tmp_path / 'loss_trace.json'
    out = subject.publish(source, output, s3=object(), bucket='bucket')
    assert out['status'] == 'already_published'
    assert out['proof'] == proof
    assert json.loads(output.read_text()) == existing
    source['as_of'] = '2026-09-19T04:59:59+00:00'
    with pytest.raises(ValueError, match='refuse to overwrite or reuse'):
        subject.publish(source, output, s3=object(), bucket='bucket')


class IdentityOnly(dict):
    """A holdout row must never expose a label, probability, or evidence field."""
    def __getitem__(self, key):
        assert key == 'game_id', f'holdout field read: {key}'
        return super().__getitem__(key)

    def get(self, key, default=None):
        assert key == 'game_id', f'holdout field read: {key}'
        return super().get(key, default)


def _real_source_and_ledger():
    from ks1.platt import identity, raw_model_version
    captured = _source()
    captured.update(finals={}, final_sources=[{'sha256': 'a' * 64}], platt_model=identity())
    for index, entry in enumerate(captured['locked']):
        gid = str(index)
        entry['row'].update(game_id=gid, model_version=raw_model_version(),
                            date='2025-01-01', as_of='2025-01-01T19:00:00+00:00',
                            commence_time='2025-01-01T20:00:00+00:00', home_id='10', away_id='20')
        entry['evidence']['stored_at'] = '2025-01-01T19:50:00+00:00'
        captured['finals'][gid] = {
            'home_id': '10', 'away_id': '20', 'home_score': 5 if index == 1 else 3,
            'away_score': 4, 'completed_at': '2025-01-01T23:00:00+00:00',
        }
    admitted, _ = subject.dataset(captured, include_predecessors=True)
    ledger = {'system': 'KS1', 'as_of': captured['as_of'], 'rows': [
        dict(
            grade,
            p_home=entry['row']['p_home'],
            lock_evidence=entry['evidence'],
            home_score=captured['finals'][grade['game_id']]['home_score'],
            away_score=captured['finals'][grade['game_id']]['away_score'],
            final_evidence=captured['final_sources'],
        )
        for grade, entry in zip(admitted, captured['locked'])
    ]}
    return captured, ledger


def test_holdout_is_removed_before_real_admission_and_diagnostics(monkeypatch):
    source, ledger = _real_source_and_ledger()
    # All three surfaces are poisoned after synthesizing a valid ledger.
    # Real dataset() must see neither frozen final nor frozen prediction.
    source['locked'][1]['row'] = IdentityOnly(game_id='1')
    source['finals']['1'] = IdentityOnly(game_id='1')
    ledger['rows'][1] = IdentityOnly(game_id='1')
    source['platt_model']['label_first_seen'] = {'1': IdentityOnly(game_id='1')}
    source['committed_ledger'] = ledger
    real_dataset = subject.dataset
    def admitted(captured, **kwargs):
        assert set(captured['finals']) == {'0', '2'}
        assert {entry['row']['game_id'] for entry in captured['locked']} == {'0', '2'}
        assert '1' not in captured['platt_model']['label_first_seen']
        assert 'committed_ledger' not in captured
        return real_dataset(captured, **kwargs)
    monkeypatch.setattr(subject, 'dataset', admitted)
    out = _build(source, ledger, {'1'})
    assert [row['game_id'] for row in out['observations']] == ['0', '2']
    assert out['sample']['prospectively_reproduced_rows'] == 2
    assert out['sample']['wins'] == out['sample']['losses'] == 1
    assert out['holdout_boundary']['holdout_labels_read'] == 0


def test_holdout_only_sample_does_not_require_final_or_prediction(monkeypatch):
    source, ledger = _real_source_and_ledger()
    frozen = {row['game_id'] for row in ledger['rows']}
    ledger['rows'] = [IdentityOnly(game_id=gid) for gid in sorted(frozen)]
    source['locked'] = []
    source['finals'] = {}
    monkeypatch.setattr(subject, 'evaluate', lambda row: pytest.fail('evaluated a holdout'))
    out = _build(source, ledger, frozen)
    assert out['observations'] == []
    assert out['sample']['frozen_holdout_ids_excluded'] == 3
    assert out['sample']['prospectively_reproduced_rows'] == 0


def test_expired_finals_are_counted_outside_supported_horizon():
    source, ledger = _real_source_and_ledger()
    source['as_of'] = '2027-01-01T07:00:00+00:00'
    source['finals'] = {}
    before = deepcopy((source, ledger))
    out = _build(source, ledger, {'1'})
    assert out['sample']['outside_final_horizon_rows'] == 2
    assert out['sample']['frozen_holdout_ids_excluded'] == 1
    assert out['sample']['analyzed_non_holdout_rows'] == 0
    assert out['reproducibility_scope']['finals_start_date_et'] == '2026-01-01'
    assert out['sample']['committed_ledger_rows'] == 3
    assert (source, ledger) == before


def test_missing_final_within_supported_horizon_still_fails():
    source, ledger = _real_source_and_ledger()
    del source['finals']['0']
    with pytest.raises(ValueError, match='not prospectively reproducible'):
        _build(source, ledger, set())


@pytest.mark.parametrize('as_of,start_date,expired', [
    ('2027-01-01T04:59:59+00:00', '2025-01-01', 0),
    ('2027-01-01T05:00:00+00:00', '2026-01-01', 3),
])
def test_final_horizon_rolls_at_eastern_new_year(as_of, start_date, expired):
    source, ledger = _real_source_and_ledger()
    source['as_of'] = as_of
    out = _build(source, ledger, set())
    assert out['reproducibility_scope']['finals_start_date_et'] == start_date
    assert out['sample']['outside_final_horizon_rows'] == expired
    assert out['sample']['analyzed_non_holdout_rows'] == 3 - expired


def test_supported_games_still_trace_after_old_finals_expire():
    source, ledger = _real_source_and_ledger()
    recent = deepcopy(source)
    recent['locked'] = recent['locked'][:1]
    entry = recent['locked'][0]
    entry['row']['game_id'] = 'recent'
    for field in ('as_of', 'commence_time'):
        entry['row'][field] = entry['row'][field].replace('2025-', '2026-')
    entry['evidence']['stored_at'] = entry['evidence']['stored_at'].replace('2025-', '2026-')
    recent['finals'] = {'recent': dict(source['finals']['0'], completed_at='2026-01-01T23:00:00+00:00')}
    admitted, _ = subject.dataset(recent, include_predecessors=True)
    ledger['rows'].append(dict(
        admitted[0],
        p_home=entry['row']['p_home'],
        lock_evidence=entry['evidence'],
        home_score=recent['finals']['recent']['home_score'],
        away_score=recent['finals']['recent']['away_score'],
        final_evidence=recent['final_sources'],
    ))
    source['locked'].append(entry)
    source['finals'] = recent['finals']
    source['as_of'] = '2027-01-01T07:00:00+00:00'
    before = deepcopy((source, ledger))
    out = _build(source, ledger, set())
    assert out['sample']['outside_final_horizon_rows'] == 3
    assert [row['game_id'] for row in out['observations']] == ['recent']
    assert (source, ledger) == before


@pytest.mark.parametrize('field,value', [
    ('signature', 'changed'), ('home_win', 1), ('p_home', .7),
    ('raw_model_version', 'changed'), ('lock_evidence', {'version_id': 'changed'}),
    ('home_score', 99), ('away_score', 99),
    ('final_evidence', [{'sha256': 'changed'}]),
])
def test_non_holdout_grade_drift_still_fails(field, value):
    source, ledger = _real_source_and_ledger()
    ledger['rows'][0][field] = value
    with pytest.raises(ValueError, match='committed'):
        _build(source, ledger, set())


def test_duplicate_non_holdout_ledger_id_fails():
    source, ledger = _real_source_and_ledger()
    ledger['rows'].append(deepcopy(ledger['rows'][0]))
    with pytest.raises(ValueError, match='duplicate.*ledger'):
        _build(source, ledger, set())


def test_real_publication_is_write_once_and_only_touches_trace(monkeypatch, tmp_path):
    class Store:
        def __init__(self):
            self.objects = {'protected/predictions': b'untouched'}
            self.writes = []

        def get_object(self, Bucket, Key, **kwargs):
            if Key not in self.objects:
                raise ClientError({'Error': {'Code': 'NoSuchKey'}}, 'GetObject')
            body = self.objects[Key]
            return {'Body': io.BytesIO(body), 'ETag': hashlib.sha256(body).hexdigest()}

        def put_object(self, Bucket, Key, Body, **kwargs):
            assert kwargs['IfNoneMatch'] == '*'
            assert Key not in self.objects
            self.objects[Key] = Body
            self.writes.append(Key)
            return {}

    source, ledger = _real_source_and_ledger()
    before = deepcopy((source, ledger))
    monkeypatch.setattr(subject, 'require_main_workflow', lambda: None)
    monkeypatch.setattr(
        subject, 'verified_final_receipts',
        lambda s3, bucket, grades: _verified(source, grades),
    )
    monkeypatch.setattr(subject, 'holdout_ids', lambda: {'1'})
    monkeypatch.setattr(subject, 'latest_checkpoint', lambda *args: {
        'ledger': ledger, 'state': {'night_date': '2026-09-19', 'catchup_revision': 2},
    })
    store = Store()
    output = tmp_path / 'loss_trace.json'
    first = subject.publish(source, output, s3=store, bucket='test')
    second = subject.publish(source, output, s3=store, bucket='test')
    assert first['status'] == 'published'
    assert second['status'] == 'already_published'
    assert first['key'] == second['key']
    assert first['proof'] == second['proof']
    assert store.writes == [first['key']]
    assert first['key'].endswith('date=2026-09-19/catchup=000002/loss_trace.json')
    assert first['proof']['sha256'] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert output.read_bytes() == store.objects[first['key']]
    assert store.objects['protected/predictions'] == b'untouched'
    assert (source, ledger) == before
    # A later capture may recover or observe the same immutable checkpoint.
    # Reuse the verified trace bytes instead of rebuilding or overwriting them.
    source['as_of'] = '2026-09-19T08:00:00+00:00'
    third = subject.publish(source, output, s3=store, bucket='test')
    assert third['status'] == 'already_published'
    assert third['key'] == first['key']
    assert third['proof'] == first['proof']
    assert output.read_bytes() == store.objects[first['key']]
    assert len(store.writes) == 1


@pytest.mark.parametrize('status,expected_calls,success', [
    ('not_due_or_already_completed', 1, True),
    ('no_new_final_grades', 1, True),
    ('completed', 1, True),
    ('completed_catchup', 1, True),
    ('source_refresh_not_verified', 0, False),
    ('unexpected', 0, False),
    (None, 0, False),
])
def test_workflow_trace_runs_only_for_completed_checkpoint(tmp_path, status, expected_calls, success):
    workflow = Path('.github/workflows/mlb-research-ingestion.yml').read_text()
    step = workflow.split('      - name: Trace settled KS1', 1)[1].split('      - uses:', 1)[0]
    # Run the actual workflow shell with a publication spy and isolated files.
    # Every valid status attempts idempotent recovery of the latest checkpoint;
    # publication itself decides whether verified trace bytes already exist.
    script = dedent(step.split('        run: |\n', 1)[1]).replace('/tmp/ks1-nightly', str(tmp_path))
    script = script.replace('python -m ks1.loss_trace', 'record_publish')
    calls = tmp_path / 'publish_calls'
    wrapper = f'record_publish() {{ echo called >> "{calls}"; }}\n' + script
    if status is not None:
        (tmp_path / 'report.json').write_text(json.dumps({'status': status}))
    for capture_time in ('2026-09-19T07:00:00Z', '2026-09-19T08:00:00Z'):
        (tmp_path / 'capture.json').write_text(json.dumps({'as_of': capture_time}))
        result = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', wrapper],
                                text=True, capture_output=True)
        assert (result.returncode == 0) is success, result.stderr
    assert (len(calls.read_text().splitlines()) if calls.exists() else 0) == 2 * expected_calls
