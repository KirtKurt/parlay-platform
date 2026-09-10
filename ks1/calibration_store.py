"""KS1-only, date-scoped grading checkpoints in the existing artifact bucket."""
import hashlib
import json
import re

from ks1.features import ET, utc
from ks1.inventory import encode

PREFIX = 'mlb/ks1/predictions-v1/'


def read_json(s3, bucket, key, version=None):
    try:
        response = s3.get_object(Bucket=bucket, Key=key, **({'VersionId': version} if version else {}))
    except Exception as exc:
        if getattr(exc, 'response', {}).get('Error', {}).get('Code') in ('NoSuchKey', '404'):
            return None, None
        raise
    body = response['Body'].read()
    sha = hashlib.sha256(body).hexdigest()
    if response.get('Metadata', {}).get('sha256', sha) != sha:
        raise ValueError('calibration checkpoint checksum mismatch')
    proof = {'bucket': bucket, 'key': key, 'sha256': sha,
             'version_id': response.get('VersionId'), 'etag': response['ETag']}
    return json.loads(body), proof


def commit_json(s3, bucket, key, value):
    """Write once, then verify the stored bytes before exposing a receipt."""
    body = encode(value)
    old, proof = read_json(s3, bucket, key)
    if old is not None:
        if encode(old) != body:
            raise ValueError('refuse to overwrite a completed calibration checkpoint')
        return old, proof
    response = s3.put_object(Bucket=bucket, Key=key, Body=body, IfNoneMatch='*',
                             Metadata={'sha256': hashlib.sha256(body).hexdigest(), 'system': 'KS1'})
    stored, proof = read_json(s3, bucket, key, response.get('VersionId'))
    if stored != value or proof['sha256'] != hashlib.sha256(body).hexdigest():
        raise ValueError('calibration checkpoint readback mismatch')
    return stored, proof


def latest_checkpoint(s3, bucket, as_of):
    """The committed nightly state wins over older daily model copies."""
    date = utc(as_of).astimezone(ET).date().isoformat()
    keys = sorted(o['Key'] for page in s3.get_paginator('list_objects_v2').paginate(Bucket=bucket, Prefix=PREFIX)
                  for o in page.get('Contents', []) if re.fullmatch(
                      re.escape(PREFIX)+r'date=\d{4}-\d{2}-\d{2}/calibration_state.json', o['Key'])
                  and o['Key'].split('date=')[1].split('/')[0] <= date)
    if not keys:
        return None
    state, proof = read_json(s3, bucket, keys[-1])
    if (state.get('system') != 'KS1' or state.get('status') != 'completed'
            or utc(state['completed_at']) > utc(as_of)
            or keys[-1] != PREFIX+'date='+state['night_date']+'/calibration_state.json'):
        raise ValueError('invalid or future KS1 calibration checkpoint')
    pointer = state['ledger']
    if (pointer['bucket'] != bucket
            or pointer['key'] != PREFIX+'date='+state['night_date']+'/graded_ledger.json'):
        raise ValueError('calibration ledger escaped the KS1 date prefix')
    ledger, ledger_proof = read_json(s3, bucket, pointer['key'], pointer.get('version_id'))
    if ledger is None or ledger_proof['sha256'] != pointer['sha256']:
        raise ValueError('committed calibration ledger is missing or changed')
    return {'state': state, 'ledger': ledger, 'evidence': proof}
