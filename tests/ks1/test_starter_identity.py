from copy import deepcopy
import hashlib
import io
import json
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from botocore.exceptions import ClientError

from ks1 import sources
from ks1.features import utc
from ks1.historical_starters import KS1_PREDICTION_PREFIX, read_locked_predictions
from ks1.starter_identity import published_starter_index
from ks1.table import build

DATE = '2026-08-03'
KEY = KS1_PREDICTION_PREFIX + 'date=' + DATE + '/predictions.parquet'
TARGET = {'game_id': '3', 'date': DATE, 'commence_time': DATE + 'T20:00:00Z'}
AFTER = '2026-08-05T00:00:00Z'


def row(**changes):
    return {**TARGET, 'as_of': DATE + 'T19:40:00Z', 'home_id': '1', 'away_id': '2',
            'home_starter_id': '99', 'home_starter_name': 'Home starter',
            'away_starter_id': '199', 'away_starter_name': 'Away starter',
            'p_home': 0.91, 'home_win': True, **changes}


def proven(**changes):
    return {'row': row(**changes), 'evidence': {
        'bucket': 'test', 'key': KEY, 'version_id': 'v1',
        'stored_at': DATE + 'T19:45:00Z', 'sha256': 'a' * 64}}


def test_latest_matching_pregame_identity_preserves_provenance_without_labels():
    early = proven(as_of=DATE + 'T19:20:00Z', home_starter_id='11')
    late = proven(as_of=DATE + 'T19:51:00Z', home_starter_id='77')
    indexed = published_starter_index([proven(), late, early], fixtures=[TARGET])['3']
    assert indexed['sides']['home']['id'] == '99'
    assert indexed['sides']['away']['id'] == '199'
    assert indexed['date'] == DATE
    assert indexed['commence_time'] == TARGET['commence_time']
    assert indexed['as_of'] == DATE + 'T19:40:00Z'
    assert indexed['source']['version_id'] == 'v1'
    assert indexed['source']['stored_at'] == DATE + 'T19:45:00Z'
    assert 'p_home' not in indexed and 'home_win' not in indexed


@pytest.mark.parametrize('field,value', [
    ('stored_at', DATE + 'T19:50:00.000001Z'),
    ('stored_at', DATE + 'T19:39:59Z'),
    ('stored_at', None), ('version_id', None), ('version_id', 'null'),
    ('sha256', None),
])
def test_unproven_or_late_storage_is_rejected(field, value):
    entry = proven()
    entry['evidence'][field] = value
    assert published_starter_index([entry], fixtures=[TARGET]) == {}


def test_t10_boundary_is_inclusive_and_raw_rows_are_not_proof():
    entry = proven(as_of=DATE + 'T19:50:00Z')
    entry['evidence']['stored_at'] = DATE + 'T19:50:00Z'
    assert '3' in published_starter_index([entry], fixtures=[TARGET])
    assert published_starter_index([entry['row']], fixtures=[TARGET]) == {}


@pytest.mark.parametrize('changes', [
    {'commence_time': DATE + 'T21:00:00Z'},
    {'date': '2026-08-04', 'commence_time': '2026-08-04T20:00:00Z'},
    {'date': '2026-08-04'}, {'game_id': '4'},
])
def test_rescheduled_or_different_fixture_does_not_inherit_old_identity(changes):
    assert published_starter_index([proven()], fixtures=[{**TARGET, **changes}]) == {}


def test_fixture_filter_precedes_latest_selection_and_accepts_equivalent_offsets():
    other = proven(commence_time='2026-08-04T20:00:00Z', date='2026-08-04',
                   as_of='2026-08-04T19:40:00Z', home_starter_id='77')
    other['evidence']['stored_at'] = '2026-08-04T19:45:00Z'
    target = {**TARGET, 'commence_time': DATE + 'T16:00:00-04:00'}
    assert published_starter_index([proven(), other], fixtures=[target])['3']['sides']['home']['id'] == '99'
    with pytest.raises(ValueError, match='duplicate target'):
        published_starter_index([proven()], fixtures=[TARGET, target])
    with pytest.raises(TypeError):
        published_starter_index([proven()])


class VersionedS3:
    def __init__(self, versions):
        self.versions, self.reads = versions, []

    def get_paginator(self, operation):
        assert operation == 'list_object_versions'
        return self

    def paginate(self, **kwargs):
        assert kwargs == {'Bucket': 'test', 'Prefix': KS1_PREDICTION_PREFIX}
        for version in self.versions:
            yield {'Versions': [{k: v for k, v in version.items() if k != 'body'}]}

    def get_object(self, **kwargs):
        self.reads.append(kwargs)
        version = next(v for v in self.versions
                       if (v['Key'], v['VersionId']) == (kwargs['Key'], kwargs['VersionId']))
        if isinstance(version['body'], Exception):
            raise version['body']
        return {'Body': io.BytesIO(version['body']), 'VersionId': version['VersionId']}


def version(version_id, stored_at, *, key=KEY, rows=None, latest=True):
    body = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(rows if rows is not None else [row()]), body)
    return {'Key': key, 'VersionId': version_id, 'LastModified': utc(stored_at),
            'IsLatest': latest, 'body': body.getvalue()}


def test_storage_reader_rejects_delayed_first_publication_despite_early_as_of():
    s3 = VersionedS3([version('late', DATE + 'T19:50:01Z')])
    entries, inventory = read_locked_predictions(s3, 'test', AFTER)
    assert entries == []
    assert inventory['excluded'][0]['reason'] == 'no_original_pre_cutoff_version'
    assert s3.reads[0]['VersionId'] == 'late'


def test_storage_reader_recovers_pre_t10_version_after_unchanged_later_publication():
    retained = version('original', DATE + 'T19:50:00Z', latest=False)
    s3 = VersionedS3([retained, version('later', DATE + 'T22:00:00Z')])
    entries, _ = read_locked_predictions(s3, 'test', AFTER)
    indexed = published_starter_index(entries, fixtures=[TARGET])['3']
    assert indexed['source']['version_id'] == 'original'
    assert indexed['source']['sha256'] == hashlib.sha256(retained['body']).hexdigest()
    assert [call['VersionId'] for call in s3.reads] == ['original', 'later']


def test_storage_reader_rejects_post_cutoff_starter_replacement():
    s3 = VersionedS3([
        version('original', DATE + 'T19:45:00Z', latest=False),
        version('later', DATE + 'T22:00:00Z', rows=[row(home_starter_id='77')]),
    ])
    entries, inventory = read_locked_predictions(s3, 'test', AFTER)
    assert entries == []
    assert inventory['excluded'][0]['reason'] == 'changed_or_missing_frozen_row'


@pytest.fixture
def retained_sources(monkeypatch, tmp_path):
    report = tmp_path / 'runtime_reports'
    report.mkdir()
    (report / 'mlb_data_admission_latest.json').write_text(json.dumps({
        'historicalDevelopment': {'artifact': {
            'bucket': 'test', 'key': sources.RECONSTRUCTED + 'data.json'}}}))

    class Reader:
        def __init__(self, *_args):
            self.receipts = []

        def pointer(self, _pointer):
            return {'rows': [], 'games': [], 'schedule': []}

        def read(self, _key):
            return {'artifact': {}}

        def keys(self, *_args, **_kwargs):
            return []

    def unavailable(*_args, **_kwargs):
        raise ValueError('optional source not configured')

    monkeypatch.setattr(sources, 'ROOT', tmp_path)
    monkeypatch.setattr(sources, 'Reader', Reader)
    monkeypatch.setattr('boto3.resource', unavailable)
    return SimpleNamespace(describe_stacks=unavailable)


@pytest.mark.parametrize('failure', ['missing', 'denied', 'corrupt'])
@pytest.mark.parametrize('failed_first', [False, True])
def test_optional_prediction_reads_fail_atomically(retained_sources, failure, failed_first):
    good = version('good', DATE + 'T19:45:00Z')
    bad = version('bad', DATE + 'T19:45:00Z',
                  key=KS1_PREDICTION_PREFIX + 'date=2026-08-04/predictions.parquet')
    bad['body'] = (b'not parquet' if failure == 'corrupt' else ClientError(
        {'Error': {'Code': 'NoSuchKey' if failure == 'missing' else 'AccessDenied'}}, 'GetObject'))
    if failed_first:
        good['Key'], bad['Key'] = bad['Key'], good['Key']
    s3 = VersionedS3([good, bad])
    bundle = sources.load_existing(retained_sources, s3, 'test')
    status = next(r for r in bundle['optional_reads'] if r['source'] == KS1_PREDICTION_PREFIX)
    assert status['status'] == 'unavailable'
    assert bundle['published_predictions'] == []
    assert bundle['source_receipts'] == []
    assert bundle['schedule'] == []
    if not failed_first:
        assert [call['VersionId'] for call in s3.reads] == ['good', 'bad']


def test_optional_prediction_success_retains_bound_source_receipt(retained_sources):
    stored = version('good', DATE + 'T19:45:00Z')
    bundle = sources.load_existing(retained_sources, VersionedS3([stored]), 'test')
    assert len(bundle['published_predictions']) == 1
    assert bundle['source_receipts'] == [{'bucket': 'test', 'key': KEY,
        'versionId': 'good', 'sha256': hashlib.sha256(stored['body']).hexdigest()}]


@pytest.mark.parametrize('start', [DATE + 'T21:00:00Z', '2026-08-04T20:00:00Z'])
def test_existing_table_join_rejects_rescheduled_starter_identity(start):
    bundle = {'published_predictions': [proven()], 'schedule': [{
        'gamePk': 3, 'gameDate': start, 'season': '2026', 'gameType': 'R',
        'status': {'abstractGameState': 'Preview'},
        'teams': {side: {'team': {'id': tid, 'name': side.title()}}
                  for side, tid in (('home', 1), ('away', 2))}}]}
    before = deepcopy(bundle)
    table, report, *_ = build(bundle)
    result = table.to_pylist()[0]
    assert result['home_starter_id'] is None and result['away_starter_id'] is None
    assert {'game_id': '3', 'reason': 'published_starter_start_mismatch'} in report['exclusions']
    assert bundle == before


@pytest.mark.parametrize('prediction_date', ['2026-08-02', None])
def test_existing_table_join_rejects_date_mismatch_despite_matching_id_and_start(prediction_date):
    bundle = {'published_predictions': [proven(date=prediction_date)], 'schedule': [{
        'gamePk': 3, 'gameDate': TARGET['commence_time'], 'season': '2026', 'gameType': 'R',
        'status': {'abstractGameState': 'Preview'},
        'teams': {side: {'team': {'id': tid, 'name': side.title()}}
                  for side, tid in (('home', 1), ('away', 2))}}]}
    if prediction_date is not None:
        stored = version('v1', DATE + 'T19:45:00Z', rows=[row(date=prediction_date)],
                         key=KS1_PREDICTION_PREFIX + 'date=' + prediction_date + '/predictions.parquet')
        entries, _ = read_locked_predictions(VersionedS3([stored]), 'test', AFTER)
        assert len(entries) == 1
        bundle['published_predictions'] = entries
    table, report, *_ = build(bundle)
    result = table.to_pylist()[0]
    assert result['home_starter_id'] is None and result['away_starter_id'] is None
    assert result['pregame_version_id'] is None
    assert {'game_id': '3', 'reason': 'published_starter_date_mismatch'} in report['exclusions']
    assert utc(result['as_of_timestamp']) == utc(DATE + 'T19:50:00Z')

    bundle['published_predictions'][0]['row']['date'] = DATE
    table, report, *_ = build(bundle)
    result = table.to_pylist()[0]
    assert result['home_starter_id'] == '99' and result['away_starter_id'] == '199'
    assert result['pregame_version_id'] == 'v1'
    assert report['exclusions'] == []


def test_table_filters_wrong_date_before_latest_selection_from_versioned_partitions():
    correct = version('correct', DATE + 'T19:45:00Z')
    wrong = version('wrong', DATE + 'T19:49:00Z',
                    key=KS1_PREDICTION_PREFIX + 'date=2026-08-02/predictions.parquet',
                    rows=[row(date='2026-08-02', as_of=DATE + 'T19:48:00Z',
                              home_starter_id='77', away_starter_id='177')])
    entries, _ = read_locked_predictions(VersionedS3([correct, wrong]), 'test', AFTER)
    assert len(entries) == 2
    bundle = {'published_predictions': entries, 'schedule': [{
        'gamePk': 3, 'gameDate': TARGET['commence_time'], 'season': '2026', 'gameType': 'R',
        'status': {'abstractGameState': 'Preview'},
        'teams': {side: {'team': {'id': tid, 'name': side.title()}}
                  for side, tid in (('home', 1), ('away', 2))}}]}
    table, report, *_ = build(bundle)
    result = table.to_pylist()[0]
    assert result['home_starter_id'] == '99' and result['away_starter_id'] == '199'
    assert result['pregame_version_id'] == 'correct'
    assert utc(result['as_of_timestamp']) == utc(DATE + 'T19:40:00Z')
    assert report['exclusions'] == []
