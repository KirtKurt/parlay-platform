from copy import deepcopy
from datetime import timezone
import hashlib
import io
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import yaml

from ks1 import coverage
from ks1.coverage import build, measure
from ks1.features import utc
from ks1.platt_inputs import PREFIX, read_locked_predictions

DATE = '2026-09-11'
KEY = PREFIX+'date='+DATE+'/predictions.parquet'
NOW = DATE+'T23:55:00Z'


@pytest.fixture(autouse=True)
def audit_clock(monkeypatch):
    class Clock:
        @staticmethod
        def now(tz):
            assert tz is timezone.utc
            return utc(NOW)
    monkeypatch.setattr(coverage, 'datetime', Clock)


def game(pk, start, *, state='Scheduled'):
    return {'gamePk': pk, 'gameDate': start, 'gameType': 'R',
            'status': {'detailedState': state}}


def row(pk, as_of, start=DATE+'T20:00:00Z'):
    return {'game_id': str(pk), 'date': DATE, 'as_of': as_of,
            'commence_time': start, 'p_home': .6}


def evidence(retained, *, stored_at=None, version_id='immutable-version'):
    return [{'row': deepcopy(retained), 'evidence': {
        'bucket': 'test-bucket', 'key': KEY, 'version_id': version_id,
        'stored_at': stored_at or retained['as_of'], 'sha256': 'a'*64}}]


def test_before_cutoff_is_not_counted_as_missing_lock():
    result = measure([game(1, '2026-09-11T20:00:00Z')], [row(1, '2026-09-11T18:00:00Z')],
                     '2026-09-11', '2026-09-11T19:00:00Z')
    assert result['cutoff_reached_games'] == 0
    assert result['future_before_t10_game_ids'] == ['1']
    assert result['lock_coverage_rate'] is None


def test_preserved_pre_cutoff_prediction_counts_as_valid_lock():
    retained = row(1, '2026-09-11T19:45:00Z')
    result = measure([game(1, '2026-09-11T20:00:00Z')], [retained],
                     '2026-09-11', '2026-09-11T19:55:00Z', evidence(retained))
    assert result['valid_locked_game_ids'] == ['1']
    assert result['missing_locked_game_ids'] == []
    assert result['lock_coverage_rate'] == 1.0


def test_missing_prediction_after_cutoff_is_reported_not_backfilled():
    result = measure([game(1, '2026-09-11T20:00:00Z')], [],
                     '2026-09-11', '2026-09-11T19:55:00Z')
    assert result['valid_locked_games'] == 0
    assert result['missing_locked_game_ids'] == ['1']
    assert result['lock_coverage_rate'] == 0.0
    assert result['backfilled_after_cutoff'] is False


def test_post_cutoff_prediction_is_invalid_not_counted_as_lock():
    result = measure([game(1, '2026-09-11T20:00:00Z')], [row(1, '2026-09-11T19:51:00Z')],
                     '2026-09-11', '2026-09-11T19:55:00Z')
    assert result['invalid_post_cutoff_prediction_game_ids'] == ['1']
    assert result['valid_locked_games'] == 0
    assert result['lock_coverage_rate'] == 0.0


def test_postponed_and_cancelled_games_do_not_reduce_coverage():
    schedule = [game(1, '2026-09-11T20:00:00Z', state='Postponed'),
                game(2, '2026-09-11T20:00:00Z', state='Cancelled'),
                game(3, '2026-09-11T20:00:00Z')]
    retained = row(3, '2026-09-11T19:40:00Z')
    result = measure(schedule, [retained],
                     '2026-09-11', '2026-09-11T19:55:00Z', evidence(retained))
    assert result['cutoff_reached_games'] == 1
    assert result['valid_locked_game_ids'] == ['3']
    assert result['lock_coverage_rate'] == 1.0


def test_doubleheader_games_are_independent_ids():
    schedule = [game(10, '2026-09-11T18:00:00Z'), game(11, '2026-09-11T22:00:00Z')]
    retained = row(10, '2026-09-11T17:40:00Z', '2026-09-11T18:00:00Z')
    result = measure(schedule, [retained],
                     '2026-09-11', '2026-09-11T20:00:00Z', evidence(retained))
    assert result['valid_locked_game_ids'] == ['10']
    assert result['future_before_t10_game_ids'] == ['11']
    assert result['missing_locked_game_ids'] == []


@pytest.mark.parametrize('stored_at,version_id,valid', [
    (DATE+'T19:39:59Z', 'v1', False),  # Storage must not predate capture.
    (DATE+'T19:40:00Z', 'v1', True),
    (DATE+'T19:50:00Z', 'v1', True),   # Equality at T-10 is admissible.
    (DATE+'T19:50:01Z', 'v1', False),
    (DATE+'T19:45:00Z', 'null', False),
    (DATE+'T19:45:00Z', None, False),
    (DATE+'T19:45:00Z', '', False),
])
def test_capture_alone_cannot_prove_a_lock(stored_at, version_id, valid):
    retained = row(1, DATE+'T19:40:00Z')
    result = measure([game(1, retained['commence_time'])], [retained], DATE, NOW,
                     evidence(retained, stored_at=stored_at, version_id=version_id))
    assert result['valid_locked_game_ids'] == (['1'] if valid else [])
    assert result['missing_locked_game_ids'] == ([] if valid else ['1'])


def test_missing_or_unbound_evidence_never_counts_as_a_lock():
    retained = row(1, DATE+'T19:40:00Z')
    changed = dict(retained, p_home=.7)
    wrong_key = evidence(retained)
    wrong_key[0]['evidence']['key'] = KEY.replace(DATE, '2026-09-10')
    no_hash = evidence(retained)
    no_hash[0]['evidence']['sha256'] = ''
    for proofs in ([], evidence(changed), wrong_key, no_hash):
        result = measure([game(1, retained['commence_time'])], [retained], DATE, NOW, proofs)
        assert result['valid_locked_games'] == 0
        assert result['missing_locked_game_ids'] == ['1']


@pytest.mark.parametrize('new_start', [DATE+'T19:45:00Z', DATE+'T21:00:00Z'])
def test_schedule_revisions_do_not_revalidate_a_retained_row(new_start):
    retained = row(1, DATE+'T19:40:00Z')
    result = measure([game(1, new_start)], [retained], DATE, NOW,
                     evidence(retained, stored_at=DATE+'T19:45:00Z'))
    assert result['status'] == 'ok'
    assert result['valid_locked_game_ids'] == ['1']
    assert result['invalid_post_cutoff_prediction_game_ids'] == []


def test_later_schedule_cannot_rescue_a_row_captured_after_its_own_cutoff():
    retained = row(1, DATE+'T19:51:00Z')
    result = measure([game(1, DATE+'T21:00:00Z')], [retained], DATE, NOW,
                     evidence(retained))
    assert result['invalid_post_cutoff_prediction_game_ids'] == ['1']
    assert result['valid_locked_games'] == 0


def test_latest_schedule_alone_determines_due_and_missing():
    schedule = [game(1, DATE+'T21:00:00Z'), game(2, DATE+'T19:45:00Z')]
    # The old row's cutoff has passed, but game 1 is now scheduled later.
    retained = row(1, DATE+'T19:40:00Z')
    result = measure(schedule, [retained], DATE, DATE+'T20:00:00Z', evidence(retained))
    assert result['future_before_t10_game_ids'] == ['1']
    assert result['cutoff_reached_games'] == 1
    assert result['missing_locked_game_ids'] == ['2']
    assert result['valid_locked_games'] == 0


class VersionedS3:
    """Read-only S3 double: every unexpected API, including writes, fails."""
    def __init__(self, versions, *, deleted=False, failure=None):
        self.versions = versions
        self.deleted = deleted
        self.failure = failure
        self.reads = []
        self.listings = []

    def get_paginator(self, operation):
        assert operation == 'list_object_versions'
        return self

    def paginate(self, **kwargs):
        self.listings.append(kwargs)
        assert kwargs['Bucket'] == 'test-bucket'
        assert kwargs['Prefix'] in (PREFIX, PREFIX+'date='+DATE+'/')
        if self.failure == 'list':
            raise PermissionError('version listing unavailable')
        # Separate pages ensure that proof in an older page is considered.
        for version in reversed(self.versions):
            yield {'Versions': [{k: v for k, v in version.items() if k != 'body'}]}
        if self.deleted:
            yield {'DeleteMarkers': [{'Key': KEY, 'IsLatest': True}]}

    def get_object(self, *, Bucket, Key, VersionId):
        assert (Bucket, Key) == ('test-bucket', KEY)
        self.reads.append(VersionId)
        if self.failure == 'get':
            raise PermissionError('version read unavailable')
        return {'Body': io.BytesIO(next(v['body'] for v in self.versions if v['VersionId'] == VersionId))}


def version(rows, version_id, stored_at, *, latest=True):
    stream = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(rows), stream)
    return {'Key': KEY, 'VersionId': version_id, 'LastModified': utc(stored_at),
            'IsLatest': latest, 'body': stream.getvalue()}


def setup_artifact(tmp_path, rows, schedule, *, now=NOW):
    inputs, output = tmp_path/'inputs', tmp_path/'output'
    inputs.mkdir()
    artifact = output/('date='+DATE)
    artifact.mkdir(parents=True)
    (inputs/'capture.json').write_text(json.dumps({'date': DATE, 'as_of': now, 'bucket': 'test-bucket'}))
    (inputs/'official.json').write_text(json.dumps({'payload': {'dates': [{'games': schedule}]}}))
    (artifact/'predictions.parquet').write_bytes(version(rows, 'local', NOW)['body'])
    return inputs, output


@pytest.mark.parametrize('stored_at,valid', [
    (DATE+'T19:39:59Z', False),
    (DATE+'T19:45:00Z', True),
    (DATE+'T19:50:00Z', True),
    (DATE+'T19:50:01Z', False),
])
def test_s3_last_modified_controls_coverage_and_matches_grading(tmp_path, stored_at, valid):
    retained = row(1, DATE+'T19:40:00Z')
    inputs, output = setup_artifact(tmp_path, [retained], [game(1, retained['commence_time'])])
    s3 = VersionedS3([version([retained], 'v1', stored_at)])
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    result = build(inputs, output, s3=s3)
    assert result['status'] == 'ok'
    assert result['valid_locked_game_ids'] == (['1'] if valid else [])
    assert s3.listings == [{'Bucket': 'test-bucket', 'Prefix': PREFIX+'date='+DATE+'/'}]
    admitted, _ = read_locked_predictions(s3, 'test-bucket', NOW)
    assert result['valid_locked_game_ids'] == [e['row']['game_id'] for e in admitted]
    if valid:
        proof = result['valid_locked_evidence']['1']
        assert proof['sha256'] == hashlib.sha256(s3.versions[0]['body']).hexdigest()
        assert proof['stored_at'] == utc(stored_at).isoformat()
    else:
        assert result['storage_inventory']['excluded'][0]['reason'] == 'no_original_pre_cutoff_version'
    assert all(p.read_bytes() == body for p, body in before.items())
    assert set(p for p in tmp_path.rglob('*') if p.is_file()) - set(before) == {
        output/('date='+DATE)/'t10_coverage.json'}


@pytest.mark.parametrize('new_start', [DATE+'T19:45:00Z', DATE+'T21:00:00Z'])
def test_unchanged_lock_in_a_late_republication_retains_original_proof(tmp_path, new_start):
    retained = row(1, DATE+'T19:40:00Z')
    inputs, output = setup_artifact(tmp_path, [retained], [game(1, new_start)])
    s3 = VersionedS3([version([retained], 'original', DATE+'T19:45:00Z', latest=False),
                      version([retained], 'republished', DATE+'T23:00:00Z')])
    result = build(inputs, output, s3=s3)
    assert result['valid_locked_game_ids'] == ['1']
    assert result['valid_locked_evidence']['1']['version_id'] == 'original'
    assert result['invalid_post_cutoff_prediction_game_ids'] == []


@pytest.mark.parametrize('case', ['null_version', 'deleted', 'changed_remote', 'changed_local', 'missing_remote'])
def test_unproven_or_changed_rows_cannot_borrow_a_historical_lock(tmp_path, case):
    retained = row(1, DATE+'T19:40:00Z')
    changed = dict(retained, p_home=.7)
    versions = [version([retained], 'null' if case == 'null_version' else 'original',
                        DATE+'T19:45:00Z', latest=case not in ('changed_remote', 'missing_remote'))]
    if case in ('changed_remote', 'missing_remote'):
        versions.append(version([changed] if case == 'changed_remote' else [], 'latest', DATE+'T23:00:00Z'))
    inputs, output = setup_artifact(tmp_path, [changed if case == 'changed_local' else retained],
                                   [game(1, retained['commence_time'])])
    result = build(inputs, output, s3=VersionedS3(versions, deleted=case == 'deleted'))
    assert result['status'] == 'ok'
    assert result['valid_locked_games'] == 0
    assert result['missing_locked_game_ids'] == ['1']


@pytest.mark.parametrize('failure', ['list', 'get', 'capture', 'parquet', 'schedule'])
def test_unavailable_telemetry_retains_diagnostic_without_modifying_predictions(tmp_path, failure):
    retained = row(1, DATE+'T19:40:00Z')
    inputs, output = setup_artifact(tmp_path, [retained], [game(1, retained['commence_time'])])
    s3 = VersionedS3([version([retained], 'v1', DATE+'T19:45:00Z')], failure=failure)
    if failure == 'capture':
        (inputs/'capture.json').unlink()
    elif failure == 'parquet':
        (output/('date='+DATE)/'predictions.parquet').write_bytes(b'broken parquet')
    elif failure == 'schedule':
        (inputs/'official.json').write_text('{')
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    result = build(inputs, output, s3=s3)
    assert result['status'] == 'unavailable'
    assert 'lock_coverage_rate' not in result
    assert result['production_authority_changed'] is False
    assert result['backfilled_after_cutoff'] is False
    assert result['aws_writes'] == 0
    assert all(p.read_bytes() == body for p, body in before.items())
    reports = list(output.rglob('t10_coverage.json'))
    assert len(reports) == 1 and json.loads(reports[0].read_text()) == result


def test_hourly_telemetry_failure_is_isolated_and_artifact_upload_is_unconditional():
    workflow = yaml.load((Path(__file__).resolve().parents[2]/
                          '.github/workflows/mlb-research-ingestion.yml').read_text(), Loader=yaml.BaseLoader)
    steps = workflow['jobs']['ingest']['steps']
    publish = next(s for s in steps if s.get('id') == 'daily')
    audit = next(s for s in steps if 'python -m ks1.coverage' in s.get('run', ''))
    upload = next(s for s in steps if s.get('with', {}).get('name', '').startswith('ks1-daily-'))
    assert 'python -m ks1.daily' in publish['run'] and '--publish' in publish['run']
    assert 'ks1.coverage' not in publish['run']
    assert publish.get('continue-on-error', 'false') == 'false'
    assert audit['if'] == "${{ !cancelled() && steps.daily.outcome == 'success' }}"
    assert audit['continue-on-error'] == 'true' and int(audit['timeout-minutes']) <= 5
    assert steps.index(publish) < steps.index(audit) < steps.index(upload)
    assert upload['if'] == 'always()'


@pytest.mark.parametrize('stored_at,valid', [
    (DATE+'T19:49:30Z', True),
    (DATE+'T19:50:00Z', True),
    (DATE+'T19:50:01Z', False),
])
def test_audit_detects_cutoff_crossed_since_capture(tmp_path, stored_at, valid):
    captured_at = DATE+'T19:49:00Z'
    retained = row(1, captured_at)
    inputs, output = setup_artifact(tmp_path, [retained],
                                   [game(1, retained['commence_time'])], now=captured_at)
    result = build(inputs, output, s3=VersionedS3([version([retained], 'v1', stored_at)]))
    assert utc(result['as_of']) == utc(NOW)  # Default runtime clock, not capture.
    assert result['capture_as_of'] == captured_at
    assert result['cutoff_reached_games'] == 1
    assert result['future_before_t10_game_ids'] == []
    assert result['valid_locked_game_ids'] == (['1'] if valid else [])
    assert result['missing_locked_game_ids'] == ([] if valid else ['1'])
    assert result['lock_coverage_rate'] == (1.0 if valid else 0.0)


def test_explicit_audit_time_keeps_not_yet_due_game_future(tmp_path):
    retained = row(1, DATE+'T19:40:00Z')
    inputs, output = setup_artifact(tmp_path, [retained],
                                   [game(1, retained['commence_time'])], now=retained['as_of'])
    result = build(inputs, output, audit_as_of=DATE+'T19:49:00Z',
                   s3=VersionedS3([version([retained], 'v1', DATE+'T19:45:00Z')]))
    assert result['as_of'] == DATE+'T19:49:00Z'
    assert result['capture_as_of'] == retained['as_of']
    assert result['future_before_t10_game_ids'] == ['1']
    assert result['cutoff_reached_games'] == 0
