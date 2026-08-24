from __future__ import annotations
import json, os
from datetime import datetime, timezone
import boto3

def _parse(value: str):
    return datetime.fromisoformat(str(value).replace('Z','+00:00'))

def handler(event, context):
    request=dict(event or {})
    if request.get('action') != 'recover_collection_dlq':
        return {'ok':False,'system':'soccer_auto','reason':'EXPLICIT_RECOVERY_ACTION_REQUIRED'}
    main=os.environ['SOCCER_AUTO_COLLECTION_QUEUE_URL']
    dlq=os.environ['SOCCER_AUTO_COLLECTION_DLQ_URL']
    limit=max(1,min(5000,int(request.get('max_messages') or 5000)))
    sqs=boto3.client('sqs')
    now=datetime.now(timezone.utc)
    scanned=deleted=requeued=retired=malformed=0
    empty=0
    while scanned < limit and empty < 5:
        r=sqs.receive_message(QueueUrl=dlq,MaxNumberOfMessages=min(10,limit-scanned),VisibilityTimeout=120,WaitTimeSeconds=1,AttributeNames=['All'])
        msgs=r.get('Messages') or []
        if not msgs:
            empty+=1
            continue
        empty=0
        for m in msgs:
            scanned+=1
            actionable=False
            body={}
            try:
                body=json.loads(m.get('Body') or '{}')
                ev=body.get('event') or {}
                raw=ev.get('commence_time') or ev.get('commenceTime') or ''
                dt=_parse(raw) if raw else None
                actionable=bool(body.get('action') in {'DISCOVER_EVENT','FETCH_EVENT'} and dt and dt > now)
            except Exception:
                malformed+=1
            if actionable:
                body['dlq_recovered_at']=now.isoformat()
                body['dlq_recovery_count']=int(body.get('dlq_recovery_count') or 0)+1
                sqs.send_message(QueueUrl=main,MessageBody=json.dumps(body,separators=(',',':')),DelaySeconds=2)
                requeued+=1
            else:
                retired+=1
            sqs.delete_message(QueueUrl=dlq,ReceiptHandle=m['ReceiptHandle'])
            deleted+=1
            if scanned >= limit:
                break
    attrs=sqs.get_queue_attributes(QueueUrl=dlq,AttributeNames=['ApproximateNumberOfMessages','ApproximateNumberOfMessagesNotVisible'])['Attributes']
    return {'ok':True,'system':'soccer_auto','component':'dlq_recovery','scanned':scanned,'deleted':deleted,'requeued_future':requeued,'retired_stale':retired,'malformed':malformed,'remaining_visible':int(attrs.get('ApproximateNumberOfMessages',0)),'remaining_inflight':int(attrs.get('ApproximateNumberOfMessagesNotVisible',0))}
