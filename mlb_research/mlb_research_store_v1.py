"""Versioned, checksum-bound research storage with conditional writes and leases."""
import hashlib
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

PREFIX = 'mlb/development-data/research-v1/'


def now():
    return datetime.now(timezone.utc)


def utc(value):
    result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('timezone required')
    return result.astimezone(timezone.utc)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def error_code(exc):
    return getattr(exc, 'response', {}).get('Error', {}).get('Code')


class Store:
    def __init__(self, bucket=None, s3=None):
        import boto3
        self.bucket = bucket or os.environ['MLB_ML_ARTIFACTS_BUCKET']
        self.s3 = s3 or boto3.client('s3')

    def read(self, name, version=None):
        args = {'Bucket': self.bucket, 'Key': PREFIX + name}
        if version:
            args['VersionId'] = version
        try:
            response = self.s3.get_object(**args)
        except Exception as exc:
            if error_code(exc) in ('NoSuchKey', '404'):
                return None
            raise
        body = response['Body'].read()
        if hashlib.sha256(body).hexdigest() != response.get('Metadata', {}).get('sha256'):
            raise ValueError('research checksum mismatch: ' + name)
        return json.loads(body), response['ETag']

    def get(self, name):
        item = self.read(name)
        return item[0] if item else None

    def put(self, name, value, *, etag=None, absent=False):
        body = encoded(value)
        args = {'Bucket': self.bucket, 'Key': PREFIX + name, 'Body': body,
                'ContentType': 'application/json', 'Metadata': {'sha256': hashlib.sha256(body).hexdigest()}}
        if absent:
            args['IfNoneMatch'] = '*'
        elif etag:
            args['IfMatch'] = etag
        response = self.s3.put_object(**args)
        version = response.get('VersionId')
        if not version or version == 'null':
            raise ValueError('versioned research storage required')
        if self.read(name, version)[0] != value:
            raise ValueError('research readback mismatch')
        return {'name': name, 'versionId': version, 'sha256': digest(value)}

    def once(self, name, value):
        try:
            self.put(name, value, absent=True)
            return value
        except Exception as exc:
            if error_code(exc) not in ('PreconditionFailed', 'ConditionalRequestConflict', '412'):
                raise
            existing = self.get(name)
            if existing is None:
                raise
            return existing

    def latest(self, name, value):
        item = self.read(name)
        value = {**value, 'deploymentGitSha': os.environ.get('INQSI_DEPLOY_GIT_SHA')}
        return self.put(name, value, etag=item[1] if item else None, absent=not item)

    def artifact(self, kind, value):
        name = f'{kind}/{digest(value)}.json'
        pointer = self.put(name, value)
        return pointer

    def load(self, pointer):
        if not pointer or not pointer.get('versionId') or pointer['versionId'] == 'null':
            raise ValueError('versioned pointer required')
        item = self.read(pointer['name'], pointer['versionId'])
        if not item or digest(item[0]) != pointer['sha256']:
            raise ValueError('research artifact binding mismatch')
        return item[0]

    def keys(self, prefix):
        for page in self.s3.get_paginator('list_objects_v2').paginate(Bucket=self.bucket, Prefix=PREFIX+prefix):
            for item in page.get('Contents', []):
                yield item['Key'][len(PREFIX):]

    def acquire(self, mode, seconds=960):
        name = 'leases/' + mode + '.json'
        old = self.read(name)
        at = now()
        if old and utc(old[0]['expiresAtUtc']) > at:
            return None
        lease = {'owner': uuid.uuid4().hex, 'expiresAtUtc': (at+timedelta(seconds=seconds)).isoformat()}
        try:
            self.put(name, lease, etag=old[1] if old else None, absent=not old)
        except Exception as exc:
            if error_code(exc) in ('PreconditionFailed', 'ConditionalRequestConflict', '412'):
                return None
            raise
        return lease['owner']

    def release(self, mode, owner):
        name = 'leases/' + mode + '.json'
        old = self.read(name)
        if old and old[0]['owner'] == owner:
            self.put(name, {**old[0], 'expiresAtUtc': now().isoformat()}, etag=old[1])
