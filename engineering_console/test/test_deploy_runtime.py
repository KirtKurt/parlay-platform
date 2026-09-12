import importlib.util
from pathlib import Path
from types import SimpleNamespace
from copy import deepcopy
import unittest
from unittest.mock import patch
import contextlib
import io
import json
import tempfile

spec = importlib.util.spec_from_file_location('deploy_runtime', Path(__file__).parents[1] / 'scripts' / 'deploy_runtime.py')
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)

class DeploymentBoundaryTest(unittest.TestCase):
    def test_deployment_main_requires_the_restart_and_durable_recovery_receipt(self):
        source, digest = 'a' * 40, 'sha256:' + 'b' * 64
        image = '111111111111.dkr.ecr.us-east-1.amazonaws.com/eng-console-runtime@' + digest
        outputs = {'ClusterName': 'cluster', 'ConsoleUrl': 'https://console.example', 'ProbeLogGroupName': 'probe-logs'}
        for role in ['Controller', 'Broker', 'Publisher', 'Job', 'Probe']: outputs[role + 'TaskDefinitionArn'] = role.lower() + ':1'
        marker = {'phase': 'recovery_ready', 'source': source, 'jobId': 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
                  'executionId': 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', 'executionTask': 'arn:aws:ecs:region:account:task/job',
                  'controllerInstance': 'cccccccc-cccc-4ccc-8ccc-cccccccccccc', 'threadId': 'thread', 'eventOffset': 3}
        restart = {'activeAtRestart': True, 'oldControllerTask': 'task/controller', 'newControllerTask': 'task/replacement',
                   'executionTask': marker['executionTask'], 'taskDefinition': 'controller:1', 'imageDigest': digest}
        proof = {'verified': True, 'source': source, 'jobId': marker['jobId'], 'executionTask': marker['executionTask'],
                 'recovery': {'executionId': marker['executionId'], 'threadId': 'thread', 'previousInstance': marker['controllerInstance'],
                              'instance': 'dddddddd-dddd-4ddd-8ddd-dddddddddddd', 'eventOffset': 5, 'checkpointOffset': 4}}
        for ready in [True, False]:
            calls = 0
            def fake(*args, **kwargs):
                nonlocal calls
                if args[:2] == ('cloudformation', 'describe-stacks'):
                    return {'Stacks': [{'Outputs': [{'OutputKey': k, 'OutputValue': v} for k, v in outputs.items()]}]}
                if args[:2] in [('cloudformation', 'deploy'), ('ecs', 'wait')]: return {}
                if args[:2] == ('ecs', 'list-tasks'): return {'taskArns': ['task/' + args[args.index('--service-name') + 1].removeprefix('eng-console-')]}
                if args[:2] == ('ecs', 'run-task'): return {'tasks': [{'taskArn': 'task/probe'}]}
                self.assertEqual(args[:2], ('ecs', 'describe-tasks'))
                arn = args[args.index('--tasks') + 1]; role = arn.split('/')[-1]
                if role == 'probe': calls += 1
                status = 'STOPPED' if role == 'probe' and calls > 1 else 'RUNNING'
                return {'tasks': [{'taskArn': arn, 'taskDefinitionArn': role + ':1', 'lastStatus': status,
                                   'containers': [{'name': role, 'imageDigest': digest, 'lastStatus': status, 'exitCode': 0}]}]}
            with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
                stack.enter_context(patch.object(deploy, 'OUT', Path(directory)))
                stack.enter_context(patch.object(deploy, 'preflight', return_value=({'PrivateSubnetIds': 'a,b', 'TrustedSecurityGroupId': 'sg'}, {'Account': '111111111111'}, None)))
                stack.enter_context(patch.object(deploy, 'aws', side_effect=fake))
                stack.enter_context(patch.object(deploy, 'probe_events', return_value=([marker, proof] if ready else [proof])))
                replacement = stack.enter_context(patch.object(deploy, 'restart_controller', return_value=restart))
                rollback = stack.enter_context(patch.object(deploy, 'rollback_candidate'))
                stack.enter_context(patch.object(deploy, 'verify_single_execution', return_value=[marker['executionTask']]))
                stack.enter_context(patch.object(deploy.time, 'sleep'))
                stack.enter_context(patch('sys.argv', ['deploy', '--image', image, '--source', source]))
                stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                if ready: deploy.main()
                else:
                    with self.assertRaises(SystemExit): deploy.main()
                record = json.loads((Path(directory) / 'deployment-record.json').read_text())
                self.assertEqual(record['status'], 'verified' if ready else 'blocked')
                self.assertEqual(replacement.call_count, 1 if ready else 0)
                self.assertEqual(rollback.call_count, 0 if ready else 1)
                if ready:
                    self.assertEqual(record['controllerRestart'], restart)
                    self.assertEqual(record['runningTasks'][0]['taskArn'], 'task/replacement')

    def test_task_roles_must_be_distinct_for_every_pair_of_trusted_services(self):
        roles = {name + 'TaskRoleArn': 'arn:aws:iam::111111111111:role/eng-console-' + name.lower() for name in ['Controller', 'Broker', 'Publisher']}
        deploy.validate_trusted_task_roles(roles)
        for a, b in [('Controller', 'Broker'), ('Controller', 'Publisher'), ('Broker', 'Publisher')]:
            with self.assertRaisesRegex(ValueError, 'distinct_task_roles'):
                deploy.validate_trusted_task_roles({**roles, a + 'TaskRoleArn': roles[b + 'TaskRoleArn']})

    def test_clean_merge_alone_cannot_satisfy_recovery_evidence(self):
        marker = {'jobId': 'job', 'executionId': 'execution', 'executionTask': 'task/job', 'threadId': 'thread', 'controllerInstance': 'old', 'eventOffset': 3}
        restart = {'activeAtRestart': True, 'oldControllerTask': 'task/old', 'newControllerTask': 'task/new', 'executionTask': 'task/job'}
        proof = {'jobId': 'job', 'executionTask': 'task/job', 'recovery': {'executionId': 'execution', 'threadId': 'thread', 'previousInstance': 'old', 'instance': 'new', 'eventOffset': 5, 'checkpointOffset': 4}}
        deploy.verify_recovery_proof(proof, marker, restart)
        for absent in [None, {}]:
            with self.assertRaisesRegex(ValueError, 'not_exercised'): deploy.verify_recovery_proof(proof, absent, restart)
            with self.assertRaisesRegex(ValueError, 'not_exercised'): deploy.verify_recovery_proof(proof, marker, absent)
        with self.assertRaises(ValueError): deploy.verify_recovery_proof({**proof, 'recovery': {}}, marker, restart)
        for key, value in [('executionId', 'other'), ('threadId', 'other'), ('previousInstance', 'other'), ('instance', 'old'), ('eventOffset', 2), ('checkpointOffset', 2), ('checkpointOffset', True)]:
            bad = deepcopy(proof); bad['recovery'][key] = value
            with self.assertRaisesRegex(ValueError, 'proof_mismatch'): deploy.verify_recovery_proof(bad, marker, restart)

    def test_recovery_restarts_only_the_verified_controller_while_coding_is_active(self):
        outputs = {'ClusterName': 'cluster', 'ControllerTaskDefinitionArn': 'controller:1', 'JobTaskDefinitionArn': 'job:1'}
        marker = {'jobId': 'a' * 36, 'executionId': 'b' * 36, 'controllerInstance': 'c' * 36, 'threadId': 'thread', 'eventOffset': 2, 'executionTask': 'task/job'}
        def task(arn, role, status):
            return {'taskArn': arn, 'taskDefinitionArn': role + ':1', 'lastStatus': status, 'startedBy': marker['executionId'] if role == 'job' else 'service',
                    'containers': [{'name': role, 'imageDigest': 'digest', 'lastStatus': status, 'exitCode': 137 if status == 'STOPPED' else None}]}
        stopped = False
        def fake(*args, **kwargs):
            nonlocal stopped
            if args[:2] == ('ecs', 'stop-task'):
                self.assertEqual(args[args.index('--task') + 1], 'task/old'); stopped = True; return {}
            if args[:2] == ('ecs', 'wait'): return {}
            if args[:2] == ('ecs', 'list-tasks'):
                self.assertTrue(stopped); return {'taskArns': ['task/new']}
            arn = args[args.index('--tasks') + 1]
            return {'tasks': [task(arn, 'job' if arn == 'task/job' else 'controller', 'STOPPED' if arn == 'task/old' and stopped else 'RUNNING')]}
        with patch.object(deploy, 'aws', side_effect=fake):
            evidence = deploy.restart_controller(outputs, marker, {'taskArn': 'task/old'}, 'digest')
        self.assertEqual(evidence['newControllerTask'], 'task/new')
        self.assertTrue(evidence['activeAtRestart'])
        with patch.object(deploy, 'aws', return_value={'tasks': [task('task/job', 'job', 'STOPPED')]}):
            with self.assertRaises(ValueError): deploy.restart_controller(outputs, marker, {'taskArn': 'task/old'}, 'digest')

    def test_duplicate_coding_execution_rejects_otherwise_successful_recovery(self):
        marker = {'executionTask': 'task/job', 'executionId': 'execution'}
        outputs = {'ClusterName': 'cluster', 'JobTaskDefinitionArn': 'job:1'}
        def task(arn):
            return {'taskArn': arn, 'taskDefinitionArn': 'job:1', 'lastStatus': 'STOPPED', 'startedBy': 'execution',
                    'containers': [{'name': 'job', 'imageDigest': 'digest', 'lastStatus': 'STOPPED', 'exitCode': 0}]}
        for duplicate in [False, True]:
            arns = ['task/job', 'task/duplicate'] if duplicate else ['task/job']
            def fake(*args):
                if args[1] == 'list-tasks': return {'taskArns': arns}
                return {'tasks': [task(a) for a in arns]}
            with patch.object(deploy, 'aws', side_effect=fake):
                if duplicate:
                    with self.assertRaisesRegex(ValueError, 'duplicate'): deploy.verify_single_execution(outputs, marker, 'digest')
                else: self.assertEqual(deploy.verify_single_execution(outputs, marker, 'digest'), ['task/job'])

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
            'phase': 'recovery_ready',
            'source': source,
            'jobId': 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
            'executionId': 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
            'executionTask': 'arn:aws:ecs:us-east-1:123456789012:task/cluster/task',
            'controllerInstance': 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
            'threadId': 'thread', 'eventOffset': 3
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
