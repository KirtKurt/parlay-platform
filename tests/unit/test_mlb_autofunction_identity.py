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
    account = subject.EXPECTED_ACCOUNT
    arn = f'arn:aws:lambda:us-east-1:{account}:function:{name}'
    role = subject.STACK + '-AutoFunctionRole-abc123'
    role_arn = f'arn:aws:iam::{account}:role/{role}'
    config = dict(FunctionName=name, FunctionArn=arn, Handler=subject.HANDLER,
                  Role=role_arn, CodeSha256=subject.lambda_code_sha256(artifact), RevisionId='revision1',
                  Environment={'Variables': {'AUTO_TABLE': 'own-table', 'ODDS_API_KEY': 'must-not-leak'}})
    responses = {
        'get_caller_identity': {'Account': account, 'Arn': subject.EXPECTED_READER_ARN},
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
        'list_rules': {'Rules': [{'Name': 'hourly', 'Arn': f'arn:aws:events:us-east-1:{account}:rule/hourly', 'State': 'ENABLED', 'ScheduleExpression': 'rate(1 hour)'}]},
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
    with pytest.raises(ValueError, match='production account'):
        run(fixture)
    assert len(fixture[3]) == 1


def test_incomplete_iam_pagination_fails(fixture):
    fixture[1]['list_role_policies'] = {'PolicyNames': [], 'IsTruncated': True}
    with pytest.raises(ValueError, match='Truncated'):
        run(fixture)


def test_paginated_policies_and_targets_are_retained(fixture):
    fixture[1]['list_role_policies'] = [
        {'PolicyNames': ['one'], 'IsTruncated': True, 'Marker': 'next'},
        {'PolicyNames': ['two'], 'IsTruncated': False}]
    fixture[1]['list_targets_by_rule'] = [
        {'Targets': [{'Id': '1', 'Arn': fixture[1]['get_function']['Configuration']['FunctionArn']}], 'NextToken': 'next'},
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


def test_qualified_alias_rule_is_included(fixture):
    arn = fixture[1]['get_function']['Configuration']['FunctionArn']
    fixture[1]['list_targets_by_rule']['Targets'][0]['Arn'] = arn + ':live'
    assert run(fixture)['rules'][0]['targets'][0]['arn'] == arn + ':live'


def test_lookalike_target_is_excluded(fixture):
    arn = fixture[1]['get_function']['Configuration']['FunctionArn']
    fixture[1]['list_targets_by_rule']['Targets'][0]['Arn'] = arn + 'lookalike:live'
    assert run(fixture)['rules'] == []


def test_environment_values_must_be_proven_stack_resources(fixture):
    env = fixture[1]['get_function']['Configuration']['Environment']['Variables']
    env.update(AUTH_HEADER_PREFIX='Bearer must-not-leak', EXTERNAL_TABLE='must-not-leak')
    report = run(fixture)
    assert 'must-not-leak' not in json.dumps(report)
    assert report['resourceBindings']['AUTO_TABLE'] == {'stackResourceId': 'own-table'}
    assert report['resourceBindings']['EXTERNAL_TABLE']['redacted'] is True


def test_untrusted_reader_principal_fails_before_stack_access(fixture):
    fixture[1]['get_caller_identity']['Arn'] = 'arn:aws:iam::735707987003:user/deployer'
    with pytest.raises(ValueError, match='read-only session'):
        run(fixture)
    assert len(fixture[3]) == 1


def test_startup_failure_writes_redacted_negative_evidence(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    def unavailable(*a, **kw):
        raise ValueError('must-not-leak')
    monkeypatch.setitem(sys.modules, 'boto3', SimpleNamespace(client=unavailable))
    output = tmp_path / 'negative.json'
    monkeypatch.setattr(sys, 'argv', ['collector', '--output', str(output)])
    assert subject.main() == 1
    report = json.loads(output.read_text())
    assert report['identityVerified'] is False
    assert report['errorType'] == 'ValueError'
    assert 'must-not-leak' not in output.read_text()


def test_workflow_has_only_restricted_oidc_authentication():
    text = Path('.github/workflows/mlb-autofunction-identity-readonly.yml').read_text()
    assert text.count("- 'scripts/mlb_lambda_artifact_identity.py'") == 2
    assert 'secrets.AWS_ACCESS_KEY_ID' not in text
    assert 'secrets.AWS_SECRET_ACCESS_KEY' not in text
    assert 'create_role' not in text and 'put_role_policy' not in text
    assert 'id-token: write' in text
    assert 'role-session-name: ks1-autofunction-readonly' in text
    assert "allowed-account-ids:" not in text
    assert "Verify restricted AWS caller before repository checkout" in text
    assert 'role/ks1-autofunction-identity-reader' in text
    evidence = text.split('  evidence:')[1]
    assert evidence.index('failedStage') < evidence.index('uses: aws-actions/configure-aws-credentials')
    policy_text = evidence.split('inline-session-policy: >-\n')[1].split('\n      - ')[0].strip()
    policy = json.loads(policy_text)
    allow, deny = policy['Statement']
    assert allow['Effect'] == 'Allow' and deny['Effect'] == 'Deny'
    assert set(allow['Action']) == set(deny['NotAction'])
    assert all(a.split(':')[1].startswith(('Get', 'List', 'Describe')) for a in allow['Action'])
    assert 'lambda:InvokeFunction' not in allow['Action']
    assert len(policy_text) < 2048


def test_newer_runtime_grammar_preserves_identity_and_hashes(fixture, monkeypatch):
    def unsupported(*a, **kw):
        raise SyntaxError('new grammar')
    monkeypatch.setattr(subject.ast, 'parse', unsupported)
    report = run(fixture)
    assert report['identityVerified'] is True
    assert report['isolationAuthorized'] is False
    assert report['sourceSummary'][0]['parseStatus'] == 'unavailable_in_collector_runtime'
    assert report['sourceSummary'][0]['sha256']


@pytest.mark.parametrize('environment,status', [
    (None, 'not_returned'),
    ({}, 'not_returned'),
    ({'Variables': None}, 'malformed'),
    ({'Variables': []}, 'malformed'),
    ({'Variables': {'AUTO_TABLE': 12}}, 'malformed'),
    ({'Error': {'ErrorCode': 'KMSAccessDeniedException', 'Message': 'must-not-leak'}}, 'error'),
    ({'Error': {'ErrorCode': 'must-not-leak', 'Message': 'must-not-leak'}}, 'error'),
    ({'Error': {}, 'Variables': {'AUTO_TABLE': 'must-not-leak'}}, 'error'),
])
def test_unavailable_environment_is_not_empty_scope(fixture, environment, status):
    config = fixture[1]['get_function']['Configuration']
    if environment is None:
        config.pop('Environment')
    else:
        config['Environment'] = environment
    report = run(fixture)
    assert report['identityVerified'] is True
    assert report['isolationAuthorized'] is False
    assert report['environmentEvidence']['getFunction']['status'] == status
    assert report['environmentEvidence']['complete'] is False
    assert report['environmentKeys'] is None
    assert report['resourceBindings'] is None
    assert 'must-not-leak' not in json.dumps(report)


def test_explicit_empty_environment_is_distinct_from_unavailable(fixture):
    fixture[1]['get_function']['Configuration']['Environment'] = {'Variables': {}}
    report = run(fixture)
    assert report['environmentEvidence']['complete'] is True
    assert report['environmentKeys'] == []
    assert report['resourceBindings'] == {}
    assert report['isolationAuthorized'] is False


def test_environment_changes_are_not_silently_bound_to_old_values(fixture):
    after = copy.deepcopy(fixture[1]['get_function_configuration'])
    after['Environment']['Variables']['AUTO_TABLE'] = 'must-not-leak'
    fixture[1]['get_function_configuration'] = after
    report = run(fixture)
    assert report['environmentEvidence']['valuesMatch'] is False
    assert report['environmentEvidence']['complete'] is False
    assert report['resourceBindings'] is None
    assert 'must-not-leak' not in json.dumps(report)


def test_one_read_cannot_hide_other_environment_read_failure(fixture):
    after = copy.deepcopy(fixture[1]['get_function_configuration'])
    after.pop('Environment')
    fixture[1]['get_function_configuration'] = after
    report = run(fixture)
    assert report['environmentEvidence']['getFunction']['status'] == 'returned'
    assert report['environmentEvidence']['getFunctionConfiguration']['status'] == 'not_returned'
    assert report['environmentEvidence']['complete'] is False
    assert report['resourceBindings'] is None


def test_environment_completeness_requires_same_revision(fixture):
    fixture[1]['get_function_configuration'] = dict(fixture[1]['get_function_configuration'], RevisionId='revision2')
    report = run(fixture)
    assert report['identityVerified'] is False
    assert report['environmentEvidence']['complete'] is False
    assert report['environmentKeys'] is None


def workflow():
    import yaml
    return yaml.load(Path('.github/workflows/mlb-autofunction-identity-readonly.yml').read_text(), Loader=yaml.BaseLoader)


def test_readonly_recovery_runs_after_completed_main_deployment():
    value = workflow()
    assert value['on']['workflow_run'] == {
        'workflows': ['Deploy SAM to AWS'], 'types': ['completed'], 'branches': ['main']}
    evidence = value['jobs']['evidence']
    assert evidence['permissions'] == {'contents': 'read', 'id-token': 'write'}
    assert 'github.event.workflow_run.head_repository.full_name == github.repository' in evidence['if']
    assert "github.event.workflow_run.head_branch == 'main'" in evidence['if']
    assert "github.event.workflow_run.event == 'push'" in evidence['if']
    assert "github.event.workflow_run.event == 'workflow_dispatch'" in evidence['if']
    assert 'workflow_run.conclusion' not in evidence['if']
    # The failed post-deploy verifier must not prevent a read-only identity audit.
    checkouts = [s for s in evidence['steps'] if s.get('uses', '').startswith('actions/checkout@')]
    checkout_ref = '${{ github.event_name == 'workflow_run' && github.event.workflow_run.head_sha || github.sha }}'
    assert all(s['with']['ref'] == checkout_ref for s in checkouts)
    upload = next(s for s in evidence['steps'] if s.get('uses', '').startswith('actions/upload-artifact@'))
    assert '${{ github.run_attempt }}' in upload['with']['name']


@pytest.mark.parametrize('account,arn,allowed', [
    (subject.EXPECTED_ACCOUNT, subject.EXPECTED_READER_ARN, True),
    ('999999999999', subject.EXPECTED_READER_ARN, False),
    (subject.EXPECTED_ACCOUNT, 'arn:aws:iam::735707987003:user/deployer', False),
])
def test_workflow_principal_check_precedes_checkout(tmp_path, monkeypatch, account, arn, allowed):
    import sys
    from types import SimpleNamespace
    steps = workflow()['jobs']['evidence']['steps']
    check = next(s for s in steps if s.get('name') == 'Verify restricted AWS caller before repository checkout')
    checkout = next(s for s in steps if s.get('uses', '').startswith('actions/checkout@'))
    assert steps.index(check) < steps.index(checkout)
    calls = []
    def client(service):
        calls.append(service)
        return SimpleNamespace(get_caller_identity=lambda: {'Account': account, 'Arn': arn})
    monkeypatch.setitem(sys.modules, 'boto3', SimpleNamespace(client=client))
    path = tmp_path / 'identity.json'
    path.write_text(json.dumps({'identityVerified': False, 'isolationAuthorized': False}))
    code = check['run'].split("python - <<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
    code = code.replace('/tmp/mlb-autofunction-identity.json', str(path))
    if allowed:
        exec(compile(code, '<restricted-caller-check>', 'exec'), {})
    else:
        with pytest.raises(SystemExit, match='Restricted AWS caller mismatch'):
            exec(compile(code, '<restricted-caller-check>', 'exec'), {})
        assert json.loads(path.read_text())['failedStage'] == 'restricted_principal_binding'
    assert calls == ['sts']


@pytest.mark.parametrize('operation,field', [
    ('list_rules', 'Rules'),
    ('list_targets_by_rule', 'Targets'),
    ('list_role_policies', 'PolicyNames'),
    ('list_attached_role_policies', 'AttachedPolicies'),
    ('list_stack_resources', 'StackResourceSummaries'),
])
@pytest.mark.parametrize('payload', ['absent', None, {}, '', 0])
def test_missing_or_malformed_inventory_is_not_empty(fixture, operation, field, payload):
    fixture[1][operation] = {} if payload == 'absent' else {field: payload}
    with pytest.raises((ValueError, RuntimeError)):
        run(fixture)


@pytest.mark.parametrize('token', [0, False, '', [], {}, 42])
def test_invalid_page_token_cannot_truncate_inventory(fixture, token):
    fixture[1]['list_rules'] = {'Rules': [], 'NextToken': token}
    with pytest.raises((ValueError, RuntimeError)):
        run(fixture)


def test_malformed_target_cannot_hide_a_scheduled_writer(fixture):
    fixture[1]['list_targets_by_rule'] = {'Targets': [{'Id': 'target'}]}
    with pytest.raises((ValueError, RuntimeError)):
        run(fixture)


def test_duplicate_inventory_identity_fails_closed(fixture):
    rule = fixture[1]['list_rules']['Rules'][0]
    fixture[1]['list_rules'] = [
        {'Rules': [rule], 'NextToken': 'next'}, {'Rules': [rule]}]
    with pytest.raises((ValueError, RuntimeError)):
        run(fixture)


def test_explicit_empty_inventory_is_still_valid(fixture):
    fixture[1]['list_rules'] = {'Rules': []}
    fixture[1]['list_role_policies'] = {'PolicyNames': []}
    fixture[1]['list_attached_role_policies'] = {'AttachedPolicies': []}
    report = run(fixture)
    assert report['rules'] == []
    assert report['policies'] == []
    assert report['ruleInventoryComplete'] is True
    assert report['isolationAuthorized'] is False


@pytest.mark.parametrize('field,value', [
    ('Handler', 'unrelated.handler'),
    ('CodeSha256', 'different'),
    ('FunctionName', 'lookalike'),
])
def test_storage_bindings_require_positive_function_identity(fixture, field, value):
    fixture[1]['get_function']['Configuration'][field] = value
    report = run(fixture)
    assert report['identityVerified'] is False
    assert report['environmentEvidence']['complete'] is False
    assert report['resourceBindings'] is None


@pytest.mark.parametrize('source', [
    'import os\nx = os.environ["MLB_AUTO_STATE_TABLE"]',
    'import os as runtime_os\nx = runtime_os.environ["MLB_AUTO_STATE_TABLE"]',
    'from os import environ\nx = environ["MLB_AUTO_STATE_TABLE"]',
    'from os import environ as env\nx = env["MLB_AUTO_STATE_TABLE"]',
    'from os import getenv as read_env\nx = read_env("MLB_AUTO_STATE_TABLE")',
])
def test_source_summary_sees_direct_environment_reads(source):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('mlb_auto/storage.py', source)
    row = subject.source_summary(buffer.getvalue())[0]
    assert 'MLB_AUTO_STATE_TABLE' in row['possibleEnvironmentKeys']
    assert row['analysisScope'] == 'heuristic_ast_only_not_isolation_proof'


def test_dynamic_environment_source_is_explicitly_unknown():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('mlb_auto/storage.py', 'import os\nx = os.environ[key]\ny = os.getenv(other)\n')
    row = subject.source_summary(buffer.getvalue())[0]
    assert row['dynamicEnvironmentAccessCount'] == 2
    assert row['possibleEnvironmentKeys'] == []


def test_incomplete_inventory_failure_retains_redacted_negative_receipt(fixture, tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    clients, responses, artifact, _ = fixture
    responses['list_rules'] = {'NextToken': 'must-not-leak'}
    monkeypatch.setitem(sys.modules, 'boto3', SimpleNamespace(client=lambda service, **kw: clients[service]))
    original = subject.collect
    monkeypatch.setattr(subject, 'collect', lambda clients: original(clients, lambda location: artifact))
    output = tmp_path / 'negative.json'
    monkeypatch.setattr(sys, 'argv', ['collector', '--output', str(output)])
    assert subject.main() == 1
    report = json.loads(output.read_text())
    assert report['identityVerified'] is False
    assert report['isolationAuthorized'] is False
    assert report['awsWrites'] == report['lambdaInvocations'] == 0
    assert report['failedRead'] == 'events.list_rules:IncompleteInventoryResponse'
    assert 'must-not-leak' not in output.read_text()


@pytest.mark.parametrize('page', [
    {'Rules': [], 'IsTruncated': 'false'},
    {'Rules': [], 'IsTruncated': 0},
    {'Rules': [], 'IsTruncated': False, 'NextToken': 'secret-cursor'},
])
def test_contradictory_or_malformed_pagination_is_negative(fixture, page):
    fixture[1]['list_rules'] = page
    with pytest.raises(subject.InventoryEvidenceError) as error:
        run(fixture)
    assert 'secret-cursor' not in str(error.value)


def test_unique_cursors_cannot_bypass_page_bound(fixture, monkeypatch):
    monkeypatch.setattr(subject, 'MAX_METADATA_PAGES', 2)
    fixture[1]['list_rules'] = [
        {'Rules': [], 'NextToken': 'first'}, {'Rules': [], 'NextToken': 'second'}]
    with pytest.raises(subject.InventoryEvidenceError, match='PaginationLimitExceeded'):
        run(fixture)
    assert sum(operation == 'list_rules' for _, operation, _ in fixture[3]) == 2


def test_source_observations_do_not_expose_defaults_or_dynamic_values():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('mlb_auto/storage.py',
                         'from os import getenv as env\nx = env("TABLE_NAME", "must-not-leak")\ny = env(key="lowercase_name")\n')
    row = subject.source_summary(buffer.getvalue())[0]
    assert row['possibleEnvironmentKeys'] == ['TABLE_NAME', 'lowercase_name']
    assert row['dynamicEnvironmentAccessCount'] == 0
    assert 'must-not-leak' not in json.dumps(row)
