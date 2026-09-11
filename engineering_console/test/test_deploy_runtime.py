import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('deploy_runtime', Path(__file__).parents[1] / 'scripts' / 'deploy_runtime.py')
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)

class DeploymentBoundaryTest(unittest.TestCase):
    def test_task_observation_requires_the_requested_task_and_definition(self):
        good = {'taskArn': 'task/expected', 'taskDefinitionArn': 'definition:1', 'lastStatus': 'RUNNING'}
        self.assertEqual(deploy.observed_task({'tasks': [good]}, 'task/expected', 'definition:1'), good)
        for result in [{'tasks': []}, {'tasks': [good, good]}, {'tasks': [good], 'failures': [{}]}, {'tasks': [{**good, 'taskArn': 'task/other'}]}, {'tasks': [{**good, 'taskDefinitionArn': 'definition:2'}]}]:
            with self.assertRaises(ValueError): deploy.observed_task(result, 'task/expected', 'definition:1')

    def test_probe_success_requires_complete_matching_container_evidence(self):
        container = {'name': 'probe', 'imageDigest': 'sha256:verified', 'lastStatus': 'STOPPED', 'exitCode': 0}
        task = {'lastStatus': 'STOPPED', 'containers': [container]}
        deploy.require_task_evidence(task, 'probe', 'sha256:verified', 'STOPPED')
        for containers in [[], [container, container], [{**container, 'name': 'other'}], [{**container, 'imageDigest': 'sha256:other'}], [{**container, 'exitCode': 1}], [{**container, 'lastStatus': 'RUNNING'}]]:
            with self.assertRaises(ValueError): deploy.require_task_evidence({**task, 'containers': containers}, 'probe', 'sha256:verified', 'STOPPED')

    def test_restored_stack_can_update_its_own_recreated_mount_targets(self):
        targets = [{'SubnetId': s, 'LifeCycleState': 'available'} for s in ['subnet-a', 'subnet-b']]
        deploy.validate_restore_targets(targets, ['subnet-a', 'subnet-b'], reuse=False, managed=True)
        deploy.validate_restore_targets(targets, ['subnet-a', 'subnet-b'], reuse=True, managed=False)
        with self.assertRaises(ValueError): deploy.validate_restore_targets(targets, ['subnet-a', 'subnet-b'], reuse=False, managed=False)

    def test_external_target_reuse_requires_the_complete_available_pair(self):
        for targets in [[], [{'SubnetId': 'subnet-a', 'LifeCycleState': 'available'}], [{'SubnetId': s, 'LifeCycleState': 'creating'} for s in ['subnet-a', 'subnet-b']]]:
            with self.assertRaises(ValueError): deploy.validate_restore_targets(targets, ['subnet-a', 'subnet-b'], reuse=True, managed=False)

    def test_deploy_success_prose_is_not_parsed_as_json(self):
        with patch.object(deploy.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='Successfully created/updated stack\n', stderr='')):
            self.assertIn('Successfully', deploy.aws('cloudformation', 'deploy', text_output=True))

    def test_job_network_rejects_general_egress_and_ingress(self):
        safe = {'IpPermissions': [], 'IpPermissionsEgress': [{'IpProtocol': 'tcp', 'FromPort': 443, 'ToPort': 443, 'UserIdGroupPairs': [{'GroupId': 'sg-broker'}]}]}
        deploy.validate_job_egress(safe)
        for bad in [dict(safe, IpPermissions=[{}]), {'IpPermissionsEgress': [{'IpProtocol': '-1'}]}, {'IpPermissionsEgress': [{'IpProtocol': 'tcp', 'FromPort': 443, 'ToPort': 443, 'IpRanges': [{'CidrIp': '0.0.0.0/0'}]}]}]:
            with self.assertRaises(ValueError): deploy.validate_job_egress(bad)

    def test_trusted_task_roles_must_be_distinct(self):
        cfg = {
            'ControllerTaskRoleArn': 'arn:aws:iam::123456789012:role/controller',
            'BrokerTaskRoleArn': 'arn:aws:iam::123456789012:role/broker',
            'PublisherTaskRoleArn': 'arn:aws:iam::123456789012:role/publisher'
        }
        deploy.validate_trusted_task_roles(cfg)
        for key in deploy.TRUSTED_TASK_ROLE_KEYS:
            bad = dict(cfg)
            bad[key] = cfg['ControllerTaskRoleArn']
            if len(set(bad.values())) == 3:
                continue
            with self.assertRaises(ValueError): deploy.validate_trusted_task_roles(bad)
        with self.assertRaises(ValueError): deploy.validate_trusted_task_roles({**cfg, 'PublisherTaskRoleArn': ''})

    def test_recovery_ready_requires_one_complete_identity(self):
        source = 'a' * 40
        ready = {
            'recoveryReady': True,
            'source': source,
            'jobId': 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
            'executionId': 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
            'executionTask': 'arn:aws:ecs:us-east-1:123456789012:task/cluster/task'
        }
        self.assertEqual(deploy.select_recovery_ready([ready], source), ready)
        self.assertIsNone(deploy.select_recovery_ready([], source))
        with self.assertRaises(ValueError): deploy.select_recovery_ready([ready, dict(ready)], source)
        with self.assertRaises(ValueError): deploy.select_recovery_ready([{**ready, 'executionId': 'not-a-uuid'}], source)

    def test_recovery_execution_must_be_unique(self):
        expected = 'arn:aws:ecs:region:acct:task/one'
        deploy.require_single_execution_task([expected], expected)
        deploy.require_single_execution_task([expected, expected], expected)
        for observed in [[], [expected, 'arn:aws:ecs:region:acct:task/two']]:
            with self.assertRaises(ValueError): deploy.require_single_execution_task(observed, expected)

if __name__ == '__main__': unittest.main()
