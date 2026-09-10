"""Delegate isolated research only from the existing canonical MLB owner."""
import json
import os


def dispatch(mode):
    name=os.environ.get('MLB_RESEARCH_FUNCTION')
    if not name or mode not in ('training','selection_capture'):
        return {'status':'NOT_CONFIGURED' if not name else 'NOT_APPLICABLE'}
    import boto3
    response=boto3.client('lambda').invoke(FunctionName=name,InvocationType='Event',
        Payload=json.dumps({'mode':'train' if mode=='training' else 'capture','source':'canonical_mlb_owner'}).encode())
    if response.get('StatusCode')!=202: raise ValueError('research dispatch not accepted')
    return {'status':'DISPATCHED','mode':mode}
