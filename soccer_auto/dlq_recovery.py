from __future__ import annotations
import json, os
from datetime import datetime, timezone
import boto3

def _parse(value: str):
    return datetime.fromisoformat(str(value).replace('Z','+00:00'))

def _queue_attrs(sqs, url):
    raw = sqs.get_queue_attributes(
        QueueUrl=url,
        AttributeNames=['ApproximateNumberOfMessages','ApproximateNumberOfMessagesNotVisible'],
    )['Attributes']
    return {
        'visible': int(raw.get('ApproximateNumberOfMessages') or 0),
        'inflight': int(raw.get('ApproximateNumberOfMessagesNotVisible') or 0),
    }

def _stale(body, sent_ms, now):
    ev = body.get('event') or {}
    raw = ev.get('commence_time') or ev.get('commenceTime') or body.get('commence_time') or ''
    dt = _parse(raw) if raw else None
    sent = datetime.fromtimestamp(sent_ms / 1000, timezone.utc) if sent_ms else None
    if dt and dt <= now:
        return True
    if dt is None and sent and (now - sent).total_seconds() > 36 * 3600:
        return True
    return False

def recover_main(sqs, main, limit, now):
    scanned = retired = released = malformed = 0
    empty = 0
    while scanned < limit and empty < 4:
        resp = sqs.receive_message(
            QueueUrl=main,
            MaxNumberOfMessages=min(10, limit - scanned),
            VisibilityTimeout=30,
            WaitTimeSeconds=1,
            AttributeNames=['All'],
        )
        msgs = resp.get('Messages') or []
        if not msgs:
            empty += 1
            continue
        empty = 0
        for msg in msgs:
            scanned += 1
            stale = False
            try:
                body = json.loads(msg.get('Body') or '{}')
                sent_ms = int((msg.get('Attributes') or {}).get('SentTimestamp') or 0)
                stale = _stale(body, sent_ms, now)
            except Exception:
                malformed += 1
                stale = True
            if stale:
                sqs.delete_message(QueueUrl=main, ReceiptHandle=msg['ReceiptHandle'])
                retired += 1
            else:
                sqs.change_message_visibility(
                    QueueUrl=main,
                    ReceiptHandle=msg['ReceiptHandle'],
                    VisibilityTimeout=0,
                )
                released += 1
    return {
        'scanned': scanned,
        'retired_stale': retired,
        'released_future': released,
        'malformed': malformed,
    }

def recover_dlq(sqs, main, dlq, limit, now):
    scanned = deleted = requeued = retired = malformed = 0
    empty = 0
    while scanned < limit and empty < 5:
        resp = sqs.receive_message(
            QueueUrl=dlq,
            MaxNumberOfMessages=min(10, limit - scanned),
            VisibilityTimeout=120,
            WaitTimeSeconds=1,
            AttributeNames=['All'],
        )
        msgs = resp.get('Messages') or []
        if not msgs:
            empty += 1
            continue
        empty = 0
        for msg in msgs:
            scanned += 1
            actionable = False
            body = {}
            try:
                body = json.loads(msg.get('Body') or '{}')
                ev = body.get('event') or {}
                raw = ev.get('commence_time') or ev.get('commenceTime') or ''
                dt = _parse(raw) if raw else None
                actionable = bool(body.get('action') in {'DISCOVER_EVENT','FETCH_EVENT'} and dt and dt > now)
            except Exception:
                malformed += 1
            if actionable:
                body['dlq_recovered_at'] = now.isoformat()
                body['dlq_recovery_count'] = int(body.get('dlq_recovery_count') or 0) + 1
                sqs.send_message(QueueUrl=main, MessageBody=json.dumps(body, separators=(',', ':')), DelaySeconds=2)
                requeued += 1
            else:
                retired += 1
            sqs.delete_message(QueueUrl=dlq, ReceiptHandle=msg['ReceiptHandle'])
            deleted += 1
    return {
        'scanned': scanned,
        'deleted': deleted,
        'requeued_future': requeued,
        'retired_stale': retired,
        'malformed': malformed,
    }

def handler(event, context):
    request = dict(event or {})
    action = str(request.get('action') or '')
    if action not in {'recover_collection_dlq', 'recover_collection_main'}:
        return {'ok': False, 'system': 'soccer_auto', 'reason': 'EXPLICIT_RECOVERY_ACTION_REQUIRED'}
    main = os.environ['SOCCER_AUTO_COLLECTION_QUEUE_URL']
    dlq = os.environ['SOCCER_AUTO_COLLECTION_DLQ_URL']
    limit = max(1, min(4000, int(request.get('max_messages') or 2000)))
    sqs = boto3.client('sqs')
    now = datetime.now(timezone.utc)
    before = {'main': _queue_attrs(sqs, main), 'dlq': _queue_attrs(sqs, dlq)}
    if action == 'recover_collection_main':
        stats = recover_main(sqs, main, limit, now)
        component = 'main_queue_recovery'
    else:
        stats = recover_dlq(sqs, main, dlq, limit, now)
        component = 'dlq_recovery'
    after = {'main': _queue_attrs(sqs, main), 'dlq': _queue_attrs(sqs, dlq)}
    return {
        'ok': True,
        'system': 'soccer_auto',
        'component': component,
        'action': action,
        'before': before,
        'after': after,
        **stats,
    }
