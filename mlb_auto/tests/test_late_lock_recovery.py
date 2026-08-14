from __future__ import annotations

from datetime import datetime, timedelta, timezone

from mlb_auto import handler


SLATE = '2026-08-13'
EVENT_ID = 'mil-lad'
START = datetime(2026, 8, 14, 2, 10, tzinfo=timezone.utc)


def _prediction(predicted_winner: str, source_pull_at: datetime) -> dict:
    return {
        'event_id': EVENT_ID,
        'slate_date': SLATE,
        'away_team': 'Milwaukee Brewers',
        'home_team': 'Los Angeles Dodgers',
        'commence_time': START.isoformat(),
        'predicted_winner': predicted_winner,
        'source_pull_at': source_pull_at.isoformat(),
        'prediction_fingerprint': f'{predicted_winner}-{source_pull_at.isoformat()}',
        'official_pick': True,
        'prediction_mode': 'ML_CHAMPION',
        'model_id': 'champion-1',
    }


class FakeStore:
    def __init__(self, current: dict, snapshots: list[dict]):
        self.current = dict(current)
        self.snapshots = list(snapshots)
        self.locks: list[dict] = []
        self.archives: list[tuple[str, dict]] = []
        self.snapshot_queries = 0

    def query_predictions(self, slate: str):
        return [dict(self.current)] if slate == SLATE else []

    def query_locks(self, slate: str):
        return list(self.locks) if slate == SLATE else []

    def query_snapshots(self, slate: str, event_id: str | None = None, limit: int = 500):
        del limit
        self.snapshot_queries += 1
        if slate != SLATE or event_id != EVENT_ID:
            return []
        return list(self.snapshots)

    def put_lock_once(self, slate: str, event_id: str, item: dict):
        assert slate == SLATE
        assert event_id == EVENT_ID
        if self.locks:
            raise RuntimeError('ConditionalCheckFailedException')
        self.locks.append({
            'PK': f'MLB_AUTO#LOCKS#{slate}',
            'SK': event_id,
            **item,
        })

    def archive_json(self, key: str, payload: dict):
        self.archives.append((key, dict(payload)))


def _install(monkeypatch, store: FakeStore, now: datetime):
    monkeypatch.setattr(handler, 'Store', lambda: store)
    monkeypatch.setattr(handler, '_now', lambda: now)


def test_missed_lock_recovers_once_from_pre_cutoff_snapshot(monkeypatch):
    cutoff = START - timedelta(minutes=handler.LOCK_MINUTES)
    locked_prediction = _prediction('Los Angeles Dodgers', cutoff - timedelta(minutes=2))
    post_cutoff_current = _prediction('Milwaukee Brewers', START - timedelta(minutes=1))
    store = FakeStore(post_cutoff_current, [{
        'event_id': EVENT_ID,
        'source_pull_at': locked_prediction['source_pull_at'],
        'prediction': locked_prediction,
    }])
    now = START + timedelta(hours=2)
    _install(monkeypatch, store, now)

    first = handler.lock_due_games()

    assert first['ok'] is True
    assert first['created'] == 1
    assert first['late_recovered_count'] == 1
    assert len(store.locks) == 1
    lock = store.locks[0]
    assert lock['predicted_winner'] == 'Los Angeles Dodgers'
    assert lock['source_pull_at'] == locked_prediction['source_pull_at']
    assert lock['prediction_fingerprint'] == locked_prediction['prediction_fingerprint']
    assert lock['lock_minutes'] == handler.LOCK_MINUTES == 10
    assert lock['lock_cutoff_at'] == cutoff.isoformat()
    assert lock['locked_at'] == now.isoformat()
    assert lock['immutable'] is True
    assert lock['training_eligible'] is True
    assert lock['source_before_or_at_cutoff'] is True
    assert lock['late_recovered'] is True
    assert lock['late_recovery_reason'] == handler.LATE_LOCK_RECOVERY_REASON
    assert lock['canonical_lock_hash_version'] == handler.CANONICAL_LOCK_HASH_VERSION
    assert lock['canonical_lock_hash'] == handler._canonical_lock_hash(lock)
    assert store.archives[0][0].endswith(f"/{lock['canonical_lock_hash']}.json")

    second = handler.lock_due_games()

    assert second['created'] == 0
    assert second['late_recovered_count'] == 0
    assert len(store.locks) == 1


def test_missed_lock_rejects_post_cutoff_only_snapshot(monkeypatch):
    cutoff = START - timedelta(minutes=handler.LOCK_MINUTES)
    post_cutoff = _prediction('Milwaukee Brewers', cutoff + timedelta(seconds=1))
    store = FakeStore(post_cutoff, [{
        'event_id': EVENT_ID,
        'source_pull_at': post_cutoff['source_pull_at'],
        'prediction': post_cutoff,
    }])
    _install(monkeypatch, store, START + timedelta(hours=1))

    result = handler.lock_due_games()

    assert result['ok'] is False
    assert result['created'] == 0
    assert result['late_recovered_count'] == 0
    assert result['errors'] == [f'{EVENT_ID}:NO_PERSISTED_PRE_CUTOFF_PREDICTION']
    assert store.locks == []


def test_missed_lock_recovery_expires_after_bounded_window(monkeypatch):
    cutoff = START - timedelta(minutes=handler.LOCK_MINUTES)
    pre_cutoff = _prediction('Los Angeles Dodgers', cutoff - timedelta(minutes=1))
    store = FakeStore(pre_cutoff, [{
        'event_id': EVENT_ID,
        'source_pull_at': pre_cutoff['source_pull_at'],
        'prediction': pre_cutoff,
    }])
    _install(monkeypatch, store, START + handler.LATE_LOCK_RECOVERY_WINDOW + timedelta(seconds=1))

    result = handler.lock_due_games()

    assert result['ok'] is True
    assert result['created'] == 0
    assert result['late_recovered_count'] == 0
    assert result['late_recovery_expired_count'] == 1
    assert store.snapshot_queries == 0
    assert store.locks == []
