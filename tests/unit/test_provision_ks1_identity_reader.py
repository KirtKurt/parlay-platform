from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from scripts import provision_ks1_identity_reader as subject


class AwsError(Exception):
    def __init__(self, code, message):
        self.response = {"Error": {"Code": code, "Message": message}}


def clients(existing=None):
    sts, cf, iam = Mock(), Mock(), Mock()
    sts.get_caller_identity.return_value = {"Account": subject.ACCOUNT}
    final = {"StackId": "reader-stack-id", "StackStatus": "CREATE_COMPLETE", "Outputs": [
        {"OutputKey": "ReaderRoleArn", "OutputValue": f"arn:aws:iam::{subject.ACCOUNT}:role/ks1-autofunction-identity-reader"}]}
    first = {"Stacks": [existing]} if existing else AwsError("ValidationError", "Stack does not exist")
    cf.describe_stacks.side_effect = [first, {"Stacks": [final]}]
    cf.get_template.return_value = {"TemplateBody": json.loads(subject.TEMPLATE.read_text())}
    iam.list_open_id_connect_providers.return_value = {"OpenIDConnectProviderList": []}
    iam.get_open_id_connect_provider.return_value = {"Url": "token.actions.githubusercontent.com", "ClientIDList": ["sts.amazonaws.com"]}
    return sts, cf, iam


def test_new_reader_created_transactionally_with_optional_provider():
    sts, cf, iam = clients()
    report = subject.provision(sts, cf, iam)
    assert report['ok'] and report['operation'] == 'create'
    assert report['identityVerified'] is report['isolationAuthorized'] is False
    request = cf.create_stack.call_args.kwargs
    assert request['StackName'] == subject.STACK and request['OnFailure'] == 'ROLLBACK'
    assert request['Parameters'] == [{'ParameterKey': subject.PARAMETER, 'ParameterValue': ''}]
    cf.get_waiter.assert_called_once_with('stack_create_complete')
    assert not iam.create_role.called and not iam.put_role_policy.called


def test_existing_shared_provider_reused_without_modification():
    sts, cf, iam = clients()
    iam.list_open_id_connect_providers.return_value = {'OpenIDConnectProviderList': [{'Arn': subject.PROVIDER}]}
    subject.provision(sts, cf, iam)
    assert cf.create_stack.call_args.kwargs['Parameters'][0]['ParameterValue'] == subject.PROVIDER
    assert [c[0] for c in iam.mock_calls] == ['list_open_id_connect_providers', 'get_open_id_connect_provider']


def test_owned_provider_parameter_survives_reruns():
    sts, cf, iam = clients({'StackStatus': 'CREATE_COMPLETE', 'Parameters': [{'ParameterKey': subject.PARAMETER, 'ParameterValue': ''}]})
    cf.update_stack.side_effect = AwsError('ValidationError', 'No updates are to be performed.')
    assert subject.provision(sts, cf, iam)['operation'] == 'unchanged'
    iam.list_open_id_connect_providers.assert_not_called()
    assert cf.update_stack.call_args.kwargs['Parameters'][0]['ParameterValue'] == ''


def test_same_named_stack_cannot_hide_unrelated_resources():
    sts, cf, iam = clients({'StackStatus': 'CREATE_COMPLETE', 'Parameters': [{'ParameterKey': subject.PARAMETER, 'ParameterValue': ''}]})
    cf.get_template.return_value['TemplateBody']['Resources']['UnrelatedAlgorithm'] = {'Type': 'AWS::Lambda::Function'}
    with pytest.raises(ValueError, match='separately reviewed migration'):
        subject.provision(sts, cf, iam)
    cf.update_stack.assert_not_called()


@pytest.mark.parametrize('status', ['ROLLBACK_COMPLETE', 'CREATE_IN_PROGRESS'])
def test_failed_or_inflight_stack_is_not_overwritten(status):
    sts, cf, iam = clients({'StackStatus': status})
    with pytest.raises(ValueError, match='recovery'):
        subject.provision(sts, cf, iam)
    cf.update_stack.assert_not_called()
    cf.delete_stack.assert_not_called()


def test_account_and_audience_fail_before_mutation():
    sts, cf, iam = clients()
    sts.get_caller_identity.return_value = {'Account': '999999999999'}
    with pytest.raises(ValueError, match='account'):
        subject.provision(sts, cf, iam)
    cf.describe_stacks.assert_not_called()
    sts, cf, iam = clients()
    iam.list_open_id_connect_providers.return_value = {'OpenIDConnectProviderList': [{'Arn': subject.PROVIDER}]}
    iam.get_open_id_connect_provider.return_value = {'Url': 'token.actions.githubusercontent.com', 'ClientIDList': ['unrelated']}
    with pytest.raises(ValueError, match='audience'):
        subject.provision(sts, cf, iam)
    cf.create_stack.assert_not_called()


def test_access_denied_is_not_misclassified_as_missing_stack():
    sts, cf, iam = clients()
    cf.describe_stacks.side_effect = AwsError('AccessDenied', 'not permitted')
    with pytest.raises(AwsError):
        subject.provision(sts, cf, iam)
    cf.create_stack.assert_not_called()


def test_template_trust_and_permissions_are_restricted_to_main_metadata():
    template = json.loads(subject.TEMPLATE.read_text())
    resources = template['Resources']
    assert set(resources) == {'IdentityReaderRole', 'GitHubOidcProvider'}
    role = resources['IdentityReaderRole']['Properties']
    assert role['RoleName'] == 'ks1-autofunction-identity-reader'
    trust, = role['AssumeRolePolicyDocument']['Statement']
    assert trust['Action'] == 'sts:AssumeRoleWithWebIdentity'
    assert trust['Condition']['StringEquals'] == {
        'token.actions.githubusercontent.com:aud': 'sts.amazonaws.com',
        'token.actions.githubusercontent.com:sub': 'repo:KirtKurt/parlay-platform:ref:refs/heads/main'}
    workflow = yaml.safe_load(Path('.github/workflows/mlb-autofunction-identity-readonly.yml').read_text())
    credential_step, = [s for s in workflow['jobs']['evidence']['steps'] if s.get('uses', '').startswith('aws-actions/')]
    session_policy = json.loads(credential_step['with']['inline-session-policy'])
    assert role['Policies'][0]['PolicyDocument'] == session_policy
    allow, deny = session_policy['Statement']
    assert set(allow['Action']) == set(deny['NotAction']) and deny['Effect'] == 'Deny'
    assert all(a.split(':')[1].startswith(('Get', 'List', 'Describe')) for a in allow['Action'])
    assert 'ManagedPolicyArns' not in role
    assert resources['GitHubOidcProvider']['Condition'] == 'CreateGitHubOidcProvider'


def test_provisioning_is_only_in_mutating_deployment_workflow():
    deploy = Path('.github/workflows/deploy.yml').read_text()
    assert 'python scripts/provision_ks1_identity_reader.py --output' in deploy
    assert deploy.index('Enforce durable MLB r3 release activation') < deploy.index('Provision dedicated AutoFunction')
    audit = Path('.github/workflows/mlb-autofunction-identity-readonly.yml').read_text()
    assert 'python scripts/provision_ks1_identity_reader.py' not in audit
    assert 'secrets.AWS_' not in audit
