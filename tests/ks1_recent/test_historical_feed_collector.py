import io
import json

import pytest

from ks1 import historical_feed, train
from ks1.inventory import encode
from tests.ks1.test_historical_feed import entry
from tests.ks1_phase4.test_daily import MemoryS3


class VersionedStore(MemoryS3):
    def put_object(self, **kwargs):
        super().put_object(**kwargs)
        return {'VersionId':'test-version-1'}

    def get_object(self, **kwargs):
        return {**super().get_object(**kwargs), 'VersionId':'test-version-1'}


def test_collector_checks_provider_timestamp_and_reuses_versioned_readback(monkeypatch):
    monkeypatch.setattr(train, 'artifact_write_authorized', lambda: True)
    monkeypatch.setenv('GITHUB_REF', 'refs/heads/main')
    monkeypatch.setenv('GITHUB_EVENT_NAME', 'push')
    requests = []
    def fetch(request, timeout):
        requests.append(request.full_url)
        return io.BytesIO(encode(entry()['payload']))
    monkeypatch.setattr(historical_feed, 'urlopen', fetch)
    store = VersionedStore()
    games = [{'officialGamePk':99,'startAtUtc':'2026-08-11T20:00:00Z',
              'completedAtUtc':'2026-08-11T23:00:00Z'}]
    values, report = historical_feed.collect(store, 'retained', games)
    assert report['verified_games'] == report['provider_requests'] == 1
    assert historical_feed.feed_identity(values[0])['sides']['home']['pitcher_id'] == '104'
    assert 'timecode=20260811_195000' in requests[0]
    before = dict(store.objects)
    repeated, report = historical_feed.collect(store, 'retained', games)
    assert report['verified_games'] == 1 and report['provider_requests'] == 0
    assert values == repeated and store.objects == before and len(requests) == 1
    monkeypatch.setenv('GITHUB_REF', 'refs/pull/1/merge')
    with pytest.raises(ValueError, match='authorized KS1'):
        historical_feed.collect(store, 'retained', games)


def test_collector_never_persists_a_final_or_late_feed(monkeypatch):
    monkeypatch.setattr(train, 'artifact_write_authorized', lambda: True)
    monkeypatch.setenv('GITHUB_REF', 'refs/heads/main')
    monkeypatch.setenv('GITHUB_EVENT_NAME', 'push')
    payload = entry()['payload']
    payload['metaData']['timeStamp'] = '20260811_235000'
    monkeypatch.setattr(historical_feed, 'urlopen', lambda *a, **k: io.BytesIO(encode(payload)))
    store = MemoryS3()
    values, report = historical_feed.collect(store, 'retained', [
        {'officialGamePk':99,'startAtUtc':'2026-08-11T20:00:00Z','completedAtUtc':'2026-08-11T23:00:00Z'}])
    assert values == [] and report['errors'][0]['reason'] == 'historical_pregame_state_unavailable'
    assert not store.writes
