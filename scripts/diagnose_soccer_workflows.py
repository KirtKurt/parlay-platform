"""Read-only evidence for the September 20 isolated soccer workflow failures."""
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from time import monotonic

import boto3


def main():
    cloud = boto3.client('cloudformation')
    lam = boto3.client('lambda')
    logs = boto3.client('logs')
    dynamo = boto3.resource('dynamodb')
    stack = 'parlay-platform-soccer-auto'
    resources = cloud.list_stack_resources(StackName=stack)['StackResourceSummaries']
    report = {'read_only': True, 'functions': {}, 'tables': {}, 'api_log_reports': []}
    for logical in ('SoccerApiFunction', 'SoccerControllerFunction'):
        physical = next(r['PhysicalResourceId'] for r in resources if r['LogicalResourceId'] == logical)
        config = lam.get_function_configuration(FunctionName=physical)
        report['functions'][logical] = {k: config.get(k) for k in ('FunctionName', 'Timeout', 'MemorySize', 'CodeSha256', 'LastModified', 'State', 'LastUpdateStatus')}
        if logical == 'SoccerApiFunction':
            # Only platform reports/timeouts, never application request bodies or secrets.
            for page in logs.get_paginator('filter_log_events').paginate(
                logGroupName='/aws/lambda/' + physical,
                startTime=int(datetime.fromisoformat('2026-09-20T13:18:00+00:00').timestamp() * 1000),
                endTime=int(datetime.fromisoformat('2026-09-20T13:26:00+00:00').timestamp() * 1000),
                filterPattern='?REPORT ?"Task timed out" ?Runtime.OutOfMemory',
            ):
                report['api_log_reports'].extend({'timestamp': e['timestamp'], 'message': e['message']} for e in page.get('events', []))
    for resource in resources:
        if resource['ResourceType'] != 'AWS::DynamoDB::Table':
            continue
        logical = resource['LogicalResourceId']
        if logical not in ('SoccerEventsTable', 'SoccerLocksTable', 'SoccerSettlementsTable', 'SoccerPredictionsTable'):
            continue
        table = dynamo.Table(resource['PhysicalResourceId'])
        started = monotonic()
        counts = Counter()
        kwargs = {'ConsistentRead': True, 'ProjectionExpression': 'entity_type', 'Limit': 1000}
        total = 0
        pages = 0
        while total < 10001:
            kwargs['Limit'] = min(1000, 10001 - total)
            response = table.scan(**kwargs)
            pages += 1
            rows = response.get('Items', [])
            total += len(rows)
            counts.update(row.get('entity_type', 'MISSING') for row in rows)
            cursor = response.get('LastEvaluatedKey')
            if not cursor:
                break
            kwargs['ExclusiveStartKey'] = cursor
        report['tables'][logical] = {'rows': total, 'entities': dict(counts), 'truncated': bool(cursor), 'pages': pages, 'seconds': round(monotonic()-started, 3)}
    Path('/tmp/soccer-workflow-diagnostic.json').write_text(json.dumps(report, indent=2, default=str) + '\n')
    print(json.dumps(report, sort_keys=True, default=str))


if __name__ == '__main__':
    main()
