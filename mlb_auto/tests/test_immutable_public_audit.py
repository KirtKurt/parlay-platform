from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from mlb_auto import handler


SLATE = '2026-08-13'
ROOT = Path(__file__).resolve().parents[1]


def _lock(event_id, away, home, predicted, *, official=True, source='2026-08-14T01:20:01+00:00', cutoff='2026-08-14T01:25:00+00:00'):
    return {
        'PK': f'MLB_AUTO#LOCKS#{SLATE}',
        'SK': event_id,
        'event_id': event_id,
        'away_team': away,
        'home_team': home,
        'commence_time': '2026-08-14T02:10:00+00:00',
        'predicted_winner': predicted,
        'official_pick': official,
        'prediction_mode': 'ML_CHAMPION',
        'model_id': 'champion-1',
        'win_probability': .6,
        'source_pull_at': source,
        'lock_cutoff_at': cutoff,
        'locked_at': '2026-08-14T01:25:19+00:00',
        'lock_minutes': 45,
        'prediction_fingerprint': f'locked-{event_id}',
        'immutable': True,
        'training_eligible': True,
        'source_before_or_at_cutoff': True,
    }


def _mutable(lock, predicted, *, official=True, source='2026-08-14T02:05:01+00:00'):
    return {
        **lock,
        'PK': f'MLB_AUTO#PREDICTIONS#{SLATE}',
        'predicted_winner': predicted,
        'official_pick': official,
        'source_pull_at': source,
        'prediction_fingerprint': f'mutable-{lock["event_id"]}',
        'immutable': False,
        'pre_lock_cutoff': False,
    }


class FakeStore:
    def __init__(self, mutable=None, locks=None):
        self.mutable = list(mutable or [])
        self.locks = list(locks or [])

    def query_predictions(self, slate):
        assert slate == SLATE
        return list(self.mutable)

    def query_locks(self, slate):
        assert slate == SLATE
        return list(self.locks)


def _score(event_id, away, home, away_score, home_score):
    return {
        'id': event_id,
        'completed': True,
        'scores': [
            {'name': away, 'score': str(away_score)},
            {'name': home, 'score': str(home_score)},
        ],
    }


def test_immutable_lock_beats_repainted_mutable_public_row():
    lock = _lock('mil-lad', 'Milwaukee Brewers', 'Los Angeles Dodgers', 'Los Angeles Dodgers')
    mutable = _mutable(lock, 'Milwaukee Brewers')

    rows = handler.canonical_prediction_rows(FakeStore([mutable], [lock]), SLATE)

    assert len(rows) == 1
    assert rows[0]['predicted_winner'] == 'Los Angeles Dodgers'
    assert rows[0]['source_pull_at'] == lock['source_pull_at']
    assert rows[0]['locked'] is True
    assert rows[0]['public_record_frozen'] is True
    assert rows[0]['prediction_authority'] == 'IMMUTABLE_T45_LOCK'


def test_predictions_route_uses_immutable_authority(monkeypatch):
    lock = _lock('mil-lad', 'Milwaukee Brewers', 'Los Angeles Dodgers', 'Los Angeles Dodgers')
    mutable = _mutable(lock, 'Milwaukee Brewers')
    monkeypatch.setattr(handler, 'Store', lambda: FakeStore([mutable], [lock]))

    response = handler.handler({
        'requestContext': {'http': {'method': 'GET'}},
        'rawPath': '/prod/v1/mlb-auto/predictions',
        'queryStringParameters': {'date': SLATE},
    }, None)
    payload = json.loads(response['body'])

    assert payload['prediction_authority'] == 'IMMUTABLE_LOCK_WHEN_PRESENT'
    assert payload['configured_lock_minutes'] == 10
    assert payload['locked_count'] == 1
    assert payload['predictions'][0]['predicted_winner'] == 'Los Angeles Dodgers'
    assert payload['predictions'][0]['published_predicted_winner'] == 'Milwaukee Brewers'
    assert payload['predictions'][0]['published_lock_direction_changed'] is True


def test_audit_separates_published_picks_from_canonical_locks(monkeypatch):
    texas_lock = _lock(
        'tex-laa', 'Texas Rangers', 'Los Angeles Angels', 'Texas Rangers',
        cutoff='2026-08-14T01:23:00+00:00',
    )
    brewers_lock = _lock('mil-lad', 'Milwaukee Brewers', 'Los Angeles Dodgers', 'Los Angeles Dodgers')
    mutable = [
        _mutable(texas_lock, 'Texas Rangers'),
        _mutable(brewers_lock, 'Milwaukee Brewers'),
    ]
    scores = [
        _score('tex-laa', 'Texas Rangers', 'Los Angeles Angels', 0, 7),
        _score('mil-lad', 'Milwaukee Brewers', 'Los Angeles Dodgers', 5, 4),
    ]
    monkeypatch.setattr(handler, 'Store', lambda: FakeStore(mutable, [texas_lock, brewers_lock]))
    monkeypatch.setattr(handler, 'OddsApiClient', lambda: SimpleNamespace(
        scores=lambda days_from=3: SimpleNamespace(data=scores),
    ))

    report = handler.audit_slate(SLATE)

    assert report['historical_predictions_recomputed'] is False
    assert report['canonical_official_picks']['wins'] == 0
    assert report['canonical_official_picks']['losses'] == 2
    assert report['published_official_picks']['wins'] == 1
    assert report['published_official_picks']['losses'] == 1
    assert report['published_official_picks']['policy_compliant_count'] == 0
    assert report['public_lock_drift_count'] == 2
    mil = next(row for row in report['public_lock_drift'] if row['event_id'] == 'mil-lad')
    assert mil['locked_predicted_winner'] == 'Los Angeles Dodgers'
    assert mil['published_predicted_winner'] == 'Milwaukee Brewers'


def test_audit_fails_closed_for_postcutoff_lock_source(monkeypatch):
    lock = _lock(
        'mil-lad', 'Milwaukee Brewers', 'Los Angeles Dodgers', 'Los Angeles Dodgers',
        source='2026-08-14T01:26:00+00:00',
    )
    score = _score('mil-lad', 'Milwaukee Brewers', 'Los Angeles Dodgers', 5, 4)
    monkeypatch.setattr(handler, 'Store', lambda: FakeStore([], [lock]))
    monkeypatch.setattr(handler, 'OddsApiClient', lambda: SimpleNamespace(
        scores=lambda days_from=3: SimpleNamespace(data=[score]),
    ))

    report = handler.audit_slate(SLATE)
    row = report['canonical_official_picks']['rows'][0]

    assert row['correct'] is None
    assert row['grade_status'] == 'INVALID_AUTHORITY'
    assert 'POST_CUTOFF_SOURCE' in row['authority_errors']
    assert report['canonical_official_picks']['graded_count'] == 0


def test_postcutoff_prediction_cannot_be_official():
    champion = object()
    cutoff = datetime(2026, 8, 14, 1, 25, tzinfo=timezone.utc)

    assert handler._qualifies_official_pick_at_time(
        champion, .99, datetime(2026, 8, 14, 1, 25, tzinfo=timezone.utc), cutoff,
    )
    assert not handler._qualifies_official_pick_at_time(
        champion, .99, datetime(2026, 8, 14, 1, 25, 1, tzinfo=timezone.utc), cutoff,
    )


def test_canonical_lock_hash_detects_material_tampering():
    lock = _lock('mil-lad', 'Milwaukee Brewers', 'Los Angeles Dodgers', 'Los Angeles Dodgers')
    lock['canonical_lock_hash_version'] = handler.CANONICAL_LOCK_HASH_VERSION
    lock['canonical_lock_hash'] = handler._canonical_lock_hash(lock)

    assert handler._lock_validation(lock) == (True, [])

    lock['official_pick'] = False
    valid, errors = handler._lock_validation(lock)
    assert valid is False
    assert 'CANONICAL_LOCK_HASH_MISMATCH' in errors


def test_template_protects_lock_ledger_and_adds_failure_observability():
    template = (ROOT / 'template.yaml').read_text()

    assert "MLB_AUTO_LOCK_MINUTES: '10'" in template
    assert 'PointInTimeRecoveryEnabled: true' in template
    assert 'Sid: ImmutableLockLedger' in template
    assert 'DynamoDBCrudPolicy: {TableName: !Ref LocksTable}' not in template
    assert 'AutoFunctionErrorsAlarm:' in template
    assert 'AutoFunctionThrottlesAlarm:' in template
    assert 'HeartbeatFailedInvocationsAlarm:' in template
    assert 'LockFailedInvocationsAlarm:' in template
    assert 'SettlementFailedInvocationsAlarm:' in template
    assert 'TrainingFailedInvocationsAlarm:' in template
    assert 'RepairFailedInvocationsAlarm:' in template
    assert 'HistoricalBackfillFailedInvocationsAlarm:' in template
    assert 'AWS::SQS::Queue' not in template
