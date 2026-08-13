from __future__ import annotations

import json
import os
from decimal import Decimal
from typing import Any

import boto3


def safe(value: Any):
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {str(k): safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [safe(v) for v in value]
    if isinstance(value, tuple):
        return [safe(v) for v in value]
    return value


class Store:
    def __init__(self):
        ddb = boto3.resource('dynamodb')
        self.state = ddb.Table(os.environ['MLB_AUTO_STATE_TABLE'])
        self.snapshots = ddb.Table(os.environ['MLB_AUTO_SNAPSHOTS_TABLE'])
        self.predictions = ddb.Table(os.environ['MLB_AUTO_PREDICTIONS_TABLE'])
        self.locks = ddb.Table(os.environ['MLB_AUTO_LOCKS_TABLE'])
        self.outcomes = ddb.Table(os.environ['MLB_AUTO_OUTCOMES_TABLE'])
        self.models = ddb.Table(os.environ['MLB_AUTO_MODELS_TABLE'])
        self.s3 = boto3.client('s3')
        self.bucket = os.environ['MLB_AUTO_ARCHIVE_BUCKET']

    def get_state(self, name='controller'):
        return self.state.get_item(
            Key={'PK': 'MLB_AUTO#STATE', 'SK': name}, ConsistentRead=True
        ).get('Item') or {}

    def put_state(self, name, item):
        self.state.put_item(Item=safe({'PK': 'MLB_AUTO#STATE', 'SK': name, **item}))

    def put_snapshot(self, slate, at, item):
        self.snapshots.put_item(Item=safe({'PK': f'MLB_AUTO#SNAPSHOTS#{slate}', 'SK': at, **item}))

    def put_prediction(self, slate, event_id, item):
        self.predictions.put_item(Item=safe({'PK': f'MLB_AUTO#PREDICTIONS#{slate}', 'SK': event_id, **item}))

    def put_training_example(self, slate, event_id, item):
        self.outcomes.put_item(Item=safe({'PK': 'MLB_AUTO#TRAINING_EXAMPLES', 'SK': f'{slate}#{event_id}', **item}))

    def put_model(self, sk, item):
        self.models.put_item(Item=safe({'PK': 'MLB_AUTO#MODEL_REGISTRY', 'SK': sk, **item}))

    def put_lock_once(self, slate, event_id, item):
        self.locks.put_item(
            Item=safe({'PK': f'MLB_AUTO#LOCKS#{slate}', 'SK': event_id, **item}),
            ConditionExpression='attribute_not_exists(PK) AND attribute_not_exists(SK)',
        )

    def archive_json(self, key, payload):
        self.s3.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=json.dumps(payload, sort_keys=True, default=str).encode(),
            ContentType='application/json',
        )
