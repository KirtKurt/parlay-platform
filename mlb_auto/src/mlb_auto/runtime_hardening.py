from __future__ import annotations

import re
import time
import uuid

from botocore.exceptions import ClientError

INGEST_LEASE_SECONDS = 540
TRAINING_LEASE_SECONDS = 570


def _error(exc: Exception) -> str:
    return f'{type(exc).__name__}:{str(exc)[:500]}'


def acquire_named_lease(store, name: str, ttl_seconds: int) -> str | None:
    now = int(time.time())
    token = uuid.uuid4().hex
    try:
        store.state.update_item(
            Key={'PK': 'MLB_AUTO#STATE', 'SK': f'lease#{name}'},
            UpdateExpression='SET #owner = :owner, #expires = :expires',
            ConditionExpression='attribute_not_exists(#expires) OR #expires < :now',
            ExpressionAttributeNames={'#owner': 'owner', '#expires': 'expires_at_epoch'},
            ExpressionAttributeValues={
                ':owner': token,
                ':expires': now + int(ttl_seconds),
                ':now': now,
            },
        )
    except ClientError as exc:
        code = str((exc.response.get('Error') or {}).get('Code') or '')
        if code == 'ConditionalCheckFailedException':
            return None
        raise
    return token


def release_named_lease(store, name: str, token: str) -> None:
    try:
        store.state.delete_item(
            Key={'PK': 'MLB_AUTO#STATE', 'SK': f'lease#{name}'},
            ConditionExpression='#owner = :owner',
            ExpressionAttributeNames={'#owner': 'owner'},
            ExpressionAttributeValues={':owner': token},
        )
    except ClientError as exc:
        code = str((exc.response.get('Error') or {}).get('Code') or '')
        if code != 'ConditionalCheckFailedException':
            raise


def _compact_snapshot_writer(Store):
    original = Store.put_snapshot
    if getattr(original, '_mlb_auto_compact_raw', False):
        return

    def put_snapshot(self, slate, at, item):
        payload = dict(item or {})
        raw_event = payload.pop('event', None)
        if raw_event is not None:
            event_id = str(payload.get('event_id') or payload.get('eventId') or 'unknown')
            safe_at = re.sub(r'[^0-9A-Za-z._-]+', '-', str(at))
            key = f'mlb_auto/raw/event-detail/{slate}/{safe_at}-{event_id}.json'
            self.archive_json(key, raw_event)
            payload['raw_event_archive_key'] = key
            payload['raw_event_storage'] = 'S3_IMMUTABLE_VERSIONED'
        return original(self, slate, at, payload)

    put_snapshot._mlb_auto_compact_raw = True
    Store.put_snapshot = put_snapshot


def install(base, Store) -> None:
    if getattr(base, '_mlb_auto_runtime_hardening_installed', False):
        return

    _compact_snapshot_writer(Store)
    original_ingest = base.ingest
    original_repair = base.repair

    def guarded_ingest(*, force_reason: str | None = None):
        telemetry = Store()
        invoked_at = base._iso()
        telemetry.put_state('controller', {
            'heartbeat_at': invoked_at,
            'last_heartbeat_invoked_at': invoked_at,
            'last_heartbeat_ok': True,
            'last_heartbeat_result': 'INVOKED',
        })

        token = acquire_named_lease(telemetry, 'ingest', INGEST_LEASE_SECONDS)
        if token is None:
            telemetry.put_state('controller', {
                'last_heartbeat_ok': True,
                'last_heartbeat_result': 'INGEST_ALREADY_RUNNING',
            })
            return {
                'ok': True,
                'action': 'INGEST_ALREADY_RUNNING',
                'reason': 'INGEST_LEASE_HELD',
            }

        telemetry.put_state('controller', {
            'last_ingest_started_at': invoked_at,
            'last_ingest_ok': False,
            'last_ingest_error': '',
        })
        try:
            result = original_ingest(force_reason=force_reason)
            completed_at = base._iso()
            ok = bool(result.get('ok', True))
            telemetry.put_state('controller', {
                'last_ingest_completed_at': completed_at,
                'last_ingest_ok': ok,
                'last_ingest_error': '' if ok else str(result.get('error') or 'INGEST_RETURNED_NOT_OK')[:500],
                'last_heartbeat_ok': True,
                'last_heartbeat_result': str(result.get('action') or 'INGEST'),
            })
            return result
        except Exception as exc:
            completed_at = base._iso()
            error = _error(exc)
            telemetry.put_state('controller', {
                'last_ingest_completed_at': completed_at,
                'last_ingest_ok': False,
                'last_ingest_error': error,
                'last_heartbeat_ok': True,
                'last_heartbeat_result': 'INGEST_FAILED',
            })
            return {'ok': False, 'action': 'INGEST_FAILED', 'error': error}
        finally:
            release_named_lease(telemetry, 'ingest', token)

    def guarded_repair():
        telemetry = Store()
        started_at = base._iso()
        telemetry.put_state('repair', {
            'last_repair_at': started_at,
            'last_repair_started_at': started_at,
            'last_repair_ok': False,
            'last_repair_error': '',
        })
        try:
            result = original_repair()
        except Exception as exc:
            error = _error(exc)
            telemetry.put_state('repair', {
                'last_repair_at': base._iso(),
                'last_repair_ok': False,
                'last_repair_error': error,
            })
            return {'ok': False, 'actions': [], 'error': error}

        nested = [
            (row.get('result') or {})
            for row in (result.get('actions') or [])
            if isinstance(row, dict)
        ]
        nested_failures = [row for row in nested if row.get('ok') is False]
        ok = bool(result.get('ok', True)) and not nested_failures
        telemetry.put_state('repair', {
            'last_repair_at': base._iso(),
            'last_repair_ok': ok,
            'last_repair_error': '' if ok else str(nested_failures[:3])[:500],
        })
        if not ok:
            return {**result, 'ok': False, 'error': 'REPAIR_ACTION_FAILED'}
        return result

    base.ingest = guarded_ingest
    base.repair = guarded_repair
    base._mlb_auto_runtime_hardening_installed = True
