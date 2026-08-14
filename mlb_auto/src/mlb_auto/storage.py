from __future__ import annotations

import json
import os
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError


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


def plain(value: Any):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [plain(v) for v in value]
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

    @staticmethod
    def _query_all(table, *, limit: int | None = None, **kwargs):
        """Read every DynamoDB query page, subject only to an explicit caller limit."""
        maximum = None if limit is None else max(1, int(limit))
        rows: list[dict[str, Any]] = []
        exclusive_start_key = None
        while maximum is None or len(rows) < maximum:
            request = dict(kwargs)
            # DynamoDB Limit is evaluated rows, not returned rows when a filter is used.
            # Continue following LastEvaluatedKey until enough returned rows exist.
            request['Limit'] = min(1000, maximum - len(rows)) if maximum is not None else 1000
            if exclusive_start_key:
                request['ExclusiveStartKey'] = exclusive_start_key
            page = table.query(**request)
            rows.extend(page.get('Items') or [])
            exclusive_start_key = page.get('LastEvaluatedKey')
            if not exclusive_start_key:
                break
        return rows if maximum is None else rows[:maximum]

    @staticmethod
    def _update_fields(table, key: dict[str, str], fields: dict[str, Any]):
        payload = {str(k): safe(v) for k, v in fields.items() if str(k) not in ('PK', 'SK')}
        if not payload:
            return {}
        names: dict[str, str] = {}
        values: dict[str, Any] = {}
        assignments: list[str] = []
        for index, (name, value) in enumerate(payload.items()):
            name_token = f'#f{index}'
            value_token = f':v{index}'
            names[name_token] = name
            values[value_token] = value
            assignments.append(f'{name_token} = {value_token}')
        response = table.update_item(
            Key=key,
            UpdateExpression='SET ' + ', '.join(assignments),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
            ReturnValues='ALL_NEW',
        )
        return plain(response.get('Attributes') or {})

    def get_state(self, name='controller'):
        return plain(self.state.get_item(Key={'PK': 'MLB_AUTO#STATE', 'SK': name}, ConsistentRead=True).get('Item') or {})

    def put_state(self, name, item, *, monotonic_fields: tuple[str, ...] = ()):
        """Atomically update only supplied state fields.

        State schedules run concurrently. Full-item put operations allowed a slower invocation
        to erase newer heartbeat, inventory, settlement, or training telemetry. Known timestamp
        fields are guarded automatically so a late completion cannot move them backward.
        """
        key = {'PK': 'MLB_AUTO#STATE', 'SK': name}
        fields = {str(k): v for k, v in item.items() if str(k) not in ('PK', 'SK')}
        automatically_monotonic = {
            'heartbeat_at', 'last_pull_at', 'last_settlement_at', 'last_training_at',
            'last_training_attempt_at', 'last_repair_at', 'last_market_inventory_at',
            'last_run_at',
        }
        guarded_fields = tuple(dict.fromkeys((*monotonic_fields, *(automatically_monotonic & fields.keys()))))
        for field in guarded_fields:
            if field not in fields:
                continue
            value = safe(fields.pop(field))
            try:
                self.state.update_item(
                    Key=key,
                    UpdateExpression='SET #field = :value',
                    ConditionExpression='attribute_not_exists(#field) OR #field <= :value',
                    ExpressionAttributeNames={'#field': field},
                    ExpressionAttributeValues={':value': value},
                )
            except ClientError as exc:
                code = str((exc.response.get('Error') or {}).get('Code') or '')
                if code != 'ConditionalCheckFailedException':
                    raise
        return self._update_fields(self.state, key, fields)

    def put_snapshot(self, slate, at, item):
        event_id = str(item.get('event_id') or item.get('eventId') or '')
        sk = f'{at}#{event_id}' if event_id else at
        self.snapshots.put_item(Item=safe({'PK': f'MLB_AUTO#SNAPSHOTS#{slate}', 'SK': sk, **item}))

    def query_snapshots(self, slate, event_id=None, limit=500):
        kwargs: dict[str, Any] = {
            'KeyConditionExpression': Key('PK').eq(f'MLB_AUTO#SNAPSHOTS#{slate}'),
            'ScanIndexForward': True,
        }
        if event_id is not None:
            kwargs['FilterExpression'] = Attr('event_id').eq(str(event_id))
        rows = self._query_all(self.snapshots, limit=limit, **kwargs)
        rows = [plain(x) for x in rows]
        if event_id is not None:
            rows = [x for x in rows if str(x.get('event_id') or '') == str(event_id)]
        return rows[:max(1, int(limit))]

    def put_prediction(self, slate, event_id, item):
        self.predictions.put_item(Item=safe({'PK': f'MLB_AUTO#PREDICTIONS#{slate}', 'SK': event_id, **item}))

    def get_prediction(self, slate, event_id):
        return plain(self.predictions.get_item(Key={'PK': f'MLB_AUTO#PREDICTIONS#{slate}', 'SK': event_id}, ConsistentRead=True).get('Item') or {})

    def query_predictions(self, slate):
        rows = self._query_all(
            self.predictions,
            KeyConditionExpression=Key('PK').eq(f'MLB_AUTO#PREDICTIONS#{slate}'),
            ScanIndexForward=True,
        )
        return [plain(x) for x in rows]

    def query_locks(self, slate):
        rows = self._query_all(
            self.locks,
            KeyConditionExpression=Key('PK').eq(f'MLB_AUTO#LOCKS#{slate}'),
            ScanIndexForward=True,
        )
        return [plain(x) for x in rows]

    def get_lock(self, slate, event_id):
        return plain(self.locks.get_item(
            Key={'PK': f'MLB_AUTO#LOCKS#{slate}', 'SK': str(event_id)},
            ConsistentRead=True,
        ).get('Item') or {})

    def put_training_example(self, slate, event_id, item):
        self.outcomes.put_item(Item=safe({'PK': 'MLB_AUTO#TRAINING_EXAMPLES', 'SK': f'{slate}#{event_id}', **item}))

    def query_training_examples(self, limit=5000):
        rows = self._query_all(
            self.outcomes,
            limit=limit,
            KeyConditionExpression=Key('PK').eq('MLB_AUTO#TRAINING_EXAMPLES'),
            ScanIndexForward=True,
        )
        return [plain(x) for x in rows]

    def update_training_example_features(self, slate, event_id, features, fields=None):
        payload = dict(fields or {})
        payload['features'] = dict(features or {})
        return self._update_fields(
            self.outcomes,
            {'PK': 'MLB_AUTO#TRAINING_EXAMPLES', 'SK': f'{slate}#{event_id}'},
            payload,
        )

    def put_model(self, sk, item):
        self.models.put_item(Item=safe({'PK': 'MLB_AUTO#MODEL_REGISTRY', 'SK': sk, **item}))

    def get_model(self, sk='CHAMPION'):
        return plain(self.models.get_item(Key={'PK': 'MLB_AUTO#MODEL_REGISTRY', 'SK': sk}, ConsistentRead=True).get('Item') or {})

    def put_lock_once(self, slate, event_id, item):
        self.locks.put_item(Item=safe({'PK': f'MLB_AUTO#LOCKS#{slate}', 'SK': event_id, **item}), ConditionExpression='attribute_not_exists(PK) AND attribute_not_exists(SK)')

    def archive_json(self, key, payload):
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=json.dumps(payload, sort_keys=True, default=str).encode(), ContentType='application/json')
