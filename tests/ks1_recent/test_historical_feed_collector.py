import io
import json

import pytest

from ks1 import historical_feed, train
from ks1 import retrain_recent
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


def test_invalid_cache_body_is_a_per_game_failure(monkeypatch):
    monkeypatch.setattr(train, 'artifact_write_authorized', lambda: True)
    monkeypatch.setenv('GITHUB_REF', 'refs/heads/main')
    monkeypatch.setenv('GITHUB_EVENT_NAME', 'push')
    store = VersionedStore()
    store.objects[historical_feed.PREFIX+'game=99/timecode=20260811_195000.json'] = b'not-json'
    values, report = historical_feed.collect(store, 'retained', [
        {'officialGamePk':99,'startAtUtc':'2026-08-11T20:00:00Z','completedAtUtc':'2026-08-11T23:00:00Z'}])
    assert values == [] and report['errors'][0]['reason'] == 'invalid_cached_feed_JSONDecodeError'
    assert not store.writes


def test_pr_uses_cached_history_without_calling_main_only_collector(monkeypatch):
    monkeypatch.setenv('GITHUB_EVENT_NAME', 'pull_request')
    monkeypatch.setattr(retrain_recent, 'collect_historical_feeds', lambda *a: pytest.fail('PR attempted collection'))
    cached = [entry()]
    values, report = retrain_recent.prepare_historical_feeds({'historical_pregame_feeds':cached}, None, None)
    assert values == cached and report['provider_requests'] == 0
    assert report['collection_skipped'] == 'pull_request_read_only'


def test_collection_keeps_older_cached_training_history(monkeypatch):
    monkeypatch.setenv('GITHUB_EVENT_NAME', 'push')
    old = entry()
    new = {**entry(), 'game_id':'100'}
    monkeypatch.setattr(retrain_recent, 'collect_historical_feeds', lambda *a: ([new], {'provider_requests':1}))
    values, report = retrain_recent.prepare_historical_feeds({'historical_pregame_feeds':[old]}, None, None)
    assert {v['game_id'] for v in values} == {'99','100'}
    assert report['retained_total_games'] == 2
