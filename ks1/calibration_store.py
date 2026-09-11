"""KS1-only, date-scoped grading checkpoints in the existing artifact bucket."""
import hashlib
import json
import re

from ks1.features import ET, utc
from ks1.inventory import encode

PREFIX = 'mlb/ks1/predictions-v1/'


def checkpoint_prefix(date, revision=0):
    """Legacy nightly checkpoint, or an immutable same-day grading revision."""
    if (not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date)
            or type(revision) is not int or not 0 <= revision <= 999999):
        raise ValueError('invalid KS1 checkpoint date/revision')
    return PREFIX+'date='+date+'/' + (f'catchup={revision:06d}/' if revision else '')


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
    """Latest completed ledger; catch-ups carry the nightly models unchanged.

    A revision is visible only after its state commit. Orphan ledgers from an
    interrupted write remain invisible and are resumed by the next hourly run.
    Legacy date-scoped checkpoints remain revision zero and are never replaced.
    """
    date = utc(as_of).astimezone(ET).date().isoformat()
    keys = []
    for page in s3.get_paginator('list_objects_v2').paginate(Bucket=bucket, Prefix=PREFIX):
        for item in page.get('Contents', []):
            match = re.fullmatch(re.escape(PREFIX)+
                r'date=(\d{4}-\d{2}-\d{2})/(?:catchup=(\d{6})/)?calibration_state.json', item['Key'])
            if match and match[1] <= date:
                keys.append((match[1], int(match[2] or 0), item['Key']))
    if not keys:
        return None
    night, revision, key = max(keys)
    state, proof = read_json(s3, bucket, key)
    prefix = checkpoint_prefix(night, revision)
    if (state.get('system') != 'KS1' or state.get('status') != 'completed'
            or utc(state['completed_at']) > utc(as_of)
            or state['night_date'] != night or state.get('catchup_revision', 0) != revision
            or key != prefix+'calibration_state.json'):
        raise ValueError('invalid or future KS1 calibration checkpoint')
    pointer = state['ledger']
    if pointer['bucket'] != bucket or pointer['key'] != prefix+'graded_ledger.json':
        raise ValueError('calibration ledger escaped the KS1 date prefix')
    ledger, ledger_proof = read_json(s3, bucket, pointer['key'], pointer.get('version_id'))
    if ledger is None or ledger_proof['sha256'] != pointer['sha256']:
        raise ValueError('committed calibration ledger is missing or changed')
    return {'state': state, 'ledger': ledger, 'evidence': proof}
