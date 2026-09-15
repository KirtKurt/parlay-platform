from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import io
import json

from botocore.exceptions import ClientError
import pytest

from ks1.inventory import RESEARCH, encode
from ks1.statcast_history import load_training_statcast
from ks1.statcast_recovery import recover, recovery_pointer_key
from tests.ks1.test_statcast_history import fixture


class MemoryS3:
    def __init__(self):
        self.objects, self.versions, self.writes = {}, {}, []

    def get_object(self, Bucket, Key, VersionId=None):
        try:
            body, metadata, version = self.versions[(Key, VersionId)] if VersionId else self.objects[Key]
        except KeyError:
            raise ClientError({'Error': {'Code': 'NoSuchKey'}}, 'GetObject')
        return {'Body': io.BytesIO(body), 'Metadata': metadata, 'VersionId': version,
                'ETag': hashlib.sha256(body).hexdigest(), 'LastModified': datetime.now(timezone.utc)}

    def put_object(self, Bucket, Key, Body, Metadata, ContentType=None, IfNoneMatch=None, IfMatch=None):
        if ((IfNoneMatch and Key in self.objects)
                or (IfMatch and (Key not in self.objects
                    or hashlib.sha256(self.objects[Key][0]).hexdigest() != IfMatch))):
            raise ClientError({'Error': {'Code': 'PreconditionFailed'}}, 'PutObject')
        version = str(len(self.writes) + 1)
        value = (Body, Metadata, version)
        self.objects[Key] = value
        self.versions[Key, version] = value
        self.writes.append(Key)
        return {'VersionId': version}

    def seed(self, key, payload):
        body = encode(payload)
        self.put_object(Bucket='b', Key=key, Body=body,
                        Metadata={'sha256': hashlib.sha256(body).hexdigest()})


def authorize(monkeypatch):
    monkeypatch.setenv('GITHUB_ACTIONS', 'true')
    monkeypatch.setenv('GITHUB_REPOSITORY', 'KirtKurt/parlay-platform')
    monkeypatch.setenv('GITHUB_WORKFLOW_REF', 'KirtKurt/parlay-platform/.github/workflows/ks1-retrain-recent.yml@refs/heads/main')
    monkeypatch.setenv('GITHUB_REF', 'refs/heads/main')
    monkeypatch.setenv('GITHUB_EVENT_NAME', 'push')


def test_invalid_same_game_cache_recovers_without_editing_raw_source(monkeypatch):
    authorize(monkeypatch)
    bundle, valid, key = fixture()
    invalid = deepcopy(valid)
    invalid['rows'][-1]['woba_denom'] = ''
    s3 = MemoryS3()
    s3.seed(key, invalid)
    original = s3.objects[key]
    initial = load_training_statcast(bundle, s3, 'b')
    assert 'incomplete_pa_outcome_fields' in initial['errors'][0]['details']
    report = recover(bundle, s3, 'b', initial, fetch=lambda value: valid)
    assert report['recovered_dates'] == ['2026-09-01']
    assert report['provider_requests'] == 1
    restored = load_training_statcast(bundle, s3, 'b')
    assert restored['errors'] == []
    assert restored['verified_pitch_objects'] == 1
    assert bundle['statcast'] == valid['rows']
    assert s3.objects[key] == original
    assert all(x.startswith(RESEARCH) for x in s3.writes)
    # The final bundle keeps its prior receipt and adds the immutable recovery
    # pointer plus exact payload version for outcome-safe features.
    assert len(bundle['source_receipts']) == 3
    # A stale recovery pointer cannot qualify a changed official game set.
    bundle['schedule'].append({**bundle['schedule'][0], 'gamePk': 2})
    assert load_training_statcast(bundle, s3, 'b')['errors']
    assert '2026-09-01' not in bundle['statcast_retained_dates']


@pytest.mark.parametrize('defect', ['missing_woba', 'missing_pitch', 'duplicate',
                                   'wrong_date', 'blank_batter'])
def test_incomplete_provider_response_stays_unqualified_and_is_not_synthesized(monkeypatch, defect):
    authorize(monkeypatch)
    bundle, value, _ = fixture()
    if defect == 'missing_woba': value['rows'][-1]['woba_denom'] = ''
    elif defect == 'missing_pitch': value['rows'].pop()
    elif defect == 'duplicate': value['rows'].append(value['rows'][0])
    elif defect == 'blank_batter': value['rows'][0]['batter'] = ''
    else: value['date'] = '2026-08-31'
    s3 = MemoryS3()
    initial = load_training_statcast(bundle, s3, 'b')
    report = recover(bundle, s3, 'b', initial, fetch=lambda day: value)
    assert not report['recovered_dates']
    assert not any('/objects/' in key for key in s3.writes)
    assert json.loads(s3.objects[recovery_pointer_key('2026-09-01')][0])['verified_artifact'] is None
    assert load_training_statcast(bundle, s3, 'b')['errors']
    second = recover(bundle, s3, 'b', initial, fetch=lambda day: pytest.fail('same-day retry'))
    assert second['provider_requests'] == 0


def test_recovery_budget_and_authority_are_enforced(monkeypatch):
    bundle, _, _ = fixture()
    s3 = MemoryS3()
    report = load_training_statcast(bundle, s3, 'b')
    authorize(monkeypatch)
    assert recover(bundle, s3, 'b', report, max_dates=0)['provider_requests'] == 0
    assert not s3.writes
    monkeypatch.setenv('GITHUB_EVENT_NAME', 'pull_request')
    with pytest.raises(ValueError, match='trusted main'):
        recover(bundle, s3, 'b', report)
    assert not s3.writes


def test_provider_access_rejection_stops_recovery(monkeypatch):
    from urllib.error import HTTPError
    authorize(monkeypatch)
    bundle, _, _ = fixture()
    s3 = MemoryS3()
    report = load_training_statcast(bundle, s3, 'b')
    def forbidden(day):
        raise HTTPError('https://baseballsavant.mlb.com/', 403, 'Forbidden', {}, None)
    result = recover(bundle, s3, 'b', report, fetch=forbidden)
    assert result['provider_requests'] == 1
    assert result['stopped_reason'] == 'provider_error:HTTPError:403'
    assert not result['recovered_dates']


def test_failed_retry_keeps_existing_pointer_for_unchanged_game_set(monkeypatch):
    from urllib.error import HTTPError
    authorize(monkeypatch)
    bundle, valid, _ = fixture()
    s3 = MemoryS3()
    initial = load_training_statcast(bundle, s3, 'b')
    recover(bundle, s3, 'b', initial, fetch=lambda day: valid)
    key = recovery_pointer_key('2026-09-01')
    state = json.loads(s3.objects[key][0])
    pointer = state['verified_artifact']
    state['attempt_date'] = '2020-01-01'
    s3.seed(key, state)
    def forbidden(day):
        raise HTTPError('https://baseballsavant.mlb.com/', 403, 'Forbidden', {}, None)
    # Reuse the initial rejection to represent a transient pointer/object read
    # failure. A failed refresh must not make the valid version unreachable.
    recover(bundle, s3, 'b', initial, fetch=forbidden)
    assert json.loads(s3.objects[key][0])['verified_artifact'] == pointer
    assert load_training_statcast(bundle, s3, 'b')['errors'] == []
    bundle['schedule'].append({**bundle['schedule'][0], 'gamePk': 2})
    recover(bundle, s3, 'b', initial, fetch=forbidden)
    assert json.loads(s3.objects[key][0])['verified_artifact'] is None


def test_new_reconciliation_method_prioritizes_recent_windows(monkeypatch):
    authorize(monkeypatch)
    bundle, _, _ = fixture()
    old = deepcopy(bundle['full'][0])
    old.update(officialGamePk=2, startAtUtc='2026-08-31T18:00:00Z',
               completedAtUtc='2026-08-31T21:00:00Z')
    bundle['full'].append(old)
    bundle['schedule'].append({**bundle['schedule'][0], 'gamePk': 2,
                               'gameDate': old['startAtUtc']})
    s3 = MemoryS3()
    s3.seed(recovery_pointer_key('2026-09-01'), {
        'attempt_date': datetime.now(timezone.utc).date().isoformat(),
        'game_set_sha256': hashlib.sha256(encode([1])).hexdigest(),
        'recovery_method': 'official_pa_woba_denominator_v2'})
    attempted = []
    def fail(value):
        attempted.append(value)
        raise ValueError('unavailable test response')
    result = recover(bundle, s3, 'b', {'errors': [
        {'date': '2026-08-31', 'reason': 'gap'}, {'date': '2026-09-01', 'reason': 'gap'}]},
        fetch=fail, reconcile_official=True, max_dates=1)
    assert attempted == ['2026-09-01']
    assert result['attempts'][0]['date'] == '2026-09-01'
