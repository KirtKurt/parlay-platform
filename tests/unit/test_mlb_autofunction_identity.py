from __future__ import annotations

import copy
import io
import json
import zipfile
from pathlib import Path

import pytest

from scripts import collect_mlb_autofunction_identity as subject


class Client:
    def __init__(self, service, responses, calls):
        self.service, self.responses, self.calls = service, responses, calls

    def __getattr__(self, operation):
        def read(**kwargs):
            assert operation in subject.READ_OPERATIONS[self.service]
            self.calls.append((self.service, operation, kwargs))
            value = self.responses[operation]
            return copy.deepcopy(value.pop(0) if isinstance(value, list) else value)
        return read


@pytest.fixture
def fixture():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('mlb_auto/autonomous_handler.py',
                         'import os\nTABLE = os.getenv("AUTO_TABLE")\ndef handler(e,c): pass\n')
    artifact = buffer.getvalue()
    name = subject.STACK + '-AutoFunction-abc123'
    account = '123456789012'
    arn = f'arn:aws:lambda:us-east-1:{account}:function:{name}'
    role = subject.STACK + '-AutoFunctionRole-abc123'
    role_arn = f'arn:aws:iam::{account}:role/{role}'
    config = dict(FunctionName=name, FunctionArn=arn, Handler=subject.HANDLER,
                  Role=role_arn, CodeSha256=subject.lambda_code_sha256(artifact), RevisionId='revision1',
                  Environment={'Variables': {'AUTO_TABLE': 'own-table', 'ODDS_API_KEY': 'must-not-leak'}})
    responses = {
        'get_caller_identity': {'Account': account},
        'describe_stacks': {'Stacks': [{'StackName': subject.STACK, 'StackId': f'arn:aws:cloudformation:us-east-1:{account}:stack/{subject.STACK}/123', 'StackStatus': 'UPDATE_COMPLETE'}]},
        'list_stack_resources': {'StackResourceSummaries': [
            {'LogicalResourceId': 'AutoFunction', 'ResourceType': 'AWS::Lambda::Function', 'PhysicalResourceId': name},
            {'LogicalResourceId': 'AutoFunctionRole', 'ResourceType': 'AWS::IAM::Role', 'PhysicalResourceId': role},
            {'LogicalResourceId': 'AutoTable', 'ResourceType': 'AWS::DynamoDB::Table', 'PhysicalResourceId': 'own-table'}]},
        'get_function': {'Configuration': config, 'Code': {'Location': 'never-log-signed-url'}},
        'get_function_configuration': config,
        'get_role': {'Role': {'Arn': role_arn, 'RoleId': 'role1'}},
        'list_role_policies': {'PolicyNames': ['own-data']},
        'get_role_policy': {'PolicyDocument': {'Statement': [{'Effect': 'Allow', 'Action': 'dynamodb:PutItem', 'Resource': f'arn:aws:dynamodb:us-east-1:{account}:table/own-table'}]}},
        'list_attached_role_policies': {'AttachedPolicies': []},
        'list_rule_names_by_target': {'RuleNames': ['hourly']},
        'describe_rule': {'Arn': f'arn:aws:events:us-east-1:{account}:rule/hourly', 'State': 'ENABLED', 'ScheduleExpression': 'rate(1 hour)'},
        'list_targets_by_rule': {'Targets': [{'Id': 'target', 'Arn': arn, 'Input': '{"secret":"must-not-leak"}'}]},
    }
    calls = []
    clients = {s: Client(s, responses, calls) for s in subject.READ_OPERATIONS}
    return clients, responses, artifact, calls


def run(fixture):
    clients, _, artifact, _ = fixture
    return subject.collect(clients, lambda location: artifact)


def test_positive_owned_identity_does_not_authorize_writer(fixture):
    report = run(fixture)
    assert report['identityVerified'] is True
    assert report['isolationAuthorized'] is False
    assert report['awsWrites'] == report['providerCalls'] == report['lambdaInvocations'] == 0
    assert 'must-not-leak' not in json.dumps(report)
    assert 'never-log-signed-url' not in json.dumps(report)
    assert report['rules'][0]['targets'][0]['inputSha256']
    assert report['sourceSummary'][0]['possibleEnvironmentKeys'] == ['AUTO_TABLE']
    assert report['policies'][0]['document']['Statement'][0]['Action'] == 'dynamodb:PutItem'


@pytest.mark.parametrize('field,value,failed_check', [
    ('Handler', 'different.handler', 'expectedHandler'),
    ('FunctionName', 'lookalike', 'functionNameBinding'),
    ('CodeSha256', 'wrong', 'downloadHash'),
])
def test_mismatched_function_cannot_pass_identity(fixture, field, value, failed_check):
    fixture[1]['get_function']['Configuration'][field] = value
    report = run(fixture)
    assert report['identityVerified'] is False
    assert report['checks'][failed_check] is False


def test_revision_change_during_read_fails(fixture):
    fixture[1]['get_function_configuration'] = dict(fixture[1]['get_function_configuration'], RevisionId='revision2')
    assert run(fixture)['checks']['stableRevision'] is False


def test_same_named_external_role_is_not_stack_owned(fixture):
    fixture[1]['list_stack_resources']['StackResourceSummaries'][1]['PhysicalResourceId'] = 'different-role'
    assert run(fixture)['checks']['roleStackOwnership'] is False


def test_account_mismatch_fails(fixture):
    fixture[1]['get_caller_identity']['Account'] = '999999999999'
    assert run(fixture)['checks']['accountBinding'] is False


def test_incomplete_iam_pagination_fails(fixture):
    fixture[1]['list_role_policies'] = {'PolicyNames': [], 'IsTruncated': True}
    with pytest.raises(ValueError, match='Truncated'):
        run(fixture)


def test_paginated_policies_and_targets_are_retained(fixture):
    fixture[1]['list_role_policies'] = [
        {'PolicyNames': ['one'], 'IsTruncated': True, 'Marker': 'next'},
        {'PolicyNames': ['two'], 'IsTruncated': False}]
    fixture[1]['list_targets_by_rule'] = [
        {'Targets': [{'Id': '1', 'Arn': 'arn1'}], 'NextToken': 'next'},
        {'Targets': [{'Id': '2', 'Arn': 'arn2'}]}]
    report = run(fixture)
    assert len(report['policies']) == 2
    assert len(report['rules'][0]['targets']) == 2


def test_repeated_token_fails(fixture):
    fixture[1]['list_role_policies'] = {'PolicyNames': [], 'IsTruncated': True, 'Marker': 'loop'}
    with pytest.raises(ValueError, match='Repeated'):
        run(fixture)


def test_mutation_and_invocation_cannot_enter_dispatch(fixture):
    for operation in ['invoke', 'update_function_configuration', 'put_role_policy', 'disable_rule']:
        with pytest.raises(ValueError, match='read-only'):
            subject.call(fixture[0], 'lambda', operation)


def test_workflow_has_main_only_evidence_and_no_schedule():
    text = Path('.github/workflows/mlb-autofunction-identity-readonly.yml').read_text()
    assert "github.ref == 'refs/heads/main' && github.event_name != 'pull_request'" in text
    assert 'schedule:' not in text
    assert 'contents: read' in text
    assert 'test_mlb_autofunction_identity.py' in text
    assert 'ref: ${{ github.sha }}' in text
    assert 'if: always()' in text
