import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('deploy', Path(__file__).parents[1] / 'scripts' / 'deploy_runtime.py')
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


class RollbackTest(unittest.TestCase):
    def outputs(self):
        values = {'RuntimeStateVersion': deploy.STATE_VERSION, 'ClusterName': deploy.STACK, 'FileSystemId': 'fs-retained',
                  'ImageUri': '111111111111.dkr.ecr.us-east-1.amazonaws.com/eng-console-runtime@sha256:' + 'a' * 64,
                  'SourceSha': 'b' * 40}
        for role in ['controller', 'broker', 'publisher', 'job', 'probe']:
            values[role.title() + 'TaskDefinitionArn'] = 'arn:aws:ecs:us-east-1:111111111111:task-definition/eng-console-' + role + ':1'
        return values

    def snapshot(self):
        return {'outputs': self.outputs(), 'desiredCounts': {s: 1 for s in deploy.SERVICES},
                'parameters': [{'ParameterKey': 'ImageUri', 'ParameterValue': self.outputs()['ImageUri']}], 'templateFile': 'previous.yaml'}

    def test_snapshot_requires_compatible_state_and_captures_exact_template_and_parameters(self):
        outputs = self.outputs()
        previous = {'Stacks': [{'StackStatus': 'UPDATE_COMPLETE', 'Outputs': [{'OutputKey': k, 'OutputValue': v} for k, v in outputs.items()],
                                'Parameters': [{'ParameterKey': 'ImageUri', 'ParameterValue': outputs['ImageUri']}]}]}
        def fake(*args):
            if args[:2] == ('ecs', 'describe-services'): return {'services': [{'serviceName': s, 'desiredCount': 1} for s in deploy.SERVICES]}
            self.assertEqual(args[:2], ('cloudformation', 'get-template'))
            return {'TemplateBody': {'Resources': {'Original': {'Type': 'AWS::ECS::Cluster'}}}}
        with tempfile.TemporaryDirectory() as directory, patch.object(deploy, 'OUT', Path(directory)), patch.object(deploy, 'aws', side_effect=fake):
            snapshot = deploy.snapshot_previous(previous)
            self.assertEqual(snapshot['parameters'], previous['Stacks'][0]['Parameters'])
            self.assertIn('Original', json.loads(Path(snapshot['templateFile']).read_text())['Resources'])
            previous['Stacks'][0]['Outputs'] = []
            with self.assertRaisesRegex(ValueError, 'not_rollback_compatible'): deploy.snapshot_previous(previous)
        self.assertIsNone(deploy.snapshot_previous(None))

    def test_quiesce_stops_all_writers_before_jobs_and_confirms_empty_inventory(self):
        calls = []
        job = {'taskArn': 'task/job', 'taskDefinitionArn': self.outputs()['JobTaskDefinitionArn'], 'lastStatus': 'RUNNING'}
        with patch.object(deploy, 'aws', side_effect=lambda *a: calls.append(a)), patch.object(deploy.time, 'sleep'), patch.object(deploy, 'console_tasks', side_effect=[[job], [], []]):
            self.assertEqual(deploy.quiesce_candidate(), ['task/job'])
        self.assertEqual([c[1] for c in calls], ['update-service'] * 3 + ['wait', 'stop-task', 'wait'])
        self.assertTrue(all(c[-1] == '0' for c in calls[:3]))
        with patch.object(deploy, 'aws'), patch.object(deploy, 'console_tasks', return_value=[{**job, 'taskDefinitionArn': 'unrelated-production:1'}]):
            with self.assertRaisesRegex(ValueError, 'unknown_task'): deploy.quiesce_candidate()

    def test_verified_restore_uses_previous_image_and_preserves_shared_storage(self):
        snapshot = self.snapshot(); outputs = snapshot['outputs']; calls = []
        def fake(*args, **kwargs):
            calls.append(args)
            if args[:2] == ('cloudformation', 'describe-stacks'):
                return {'Stacks': [{'Outputs': [{'OutputKey': k, 'OutputValue': v} for k, v in outputs.items()]}]}
            if args[:2] == ('ecs', 'list-tasks'): return {'taskArns': ['task/' + args[args.index('--service-name') + 1].removeprefix('eng-console-')]}
            if args[:2] == ('ecs', 'describe-tasks'):
                arn = args[-1]; role = arn.split('/')[-1]
                return {'tasks': [{'taskArn': arn, 'taskDefinitionArn': outputs[role.title() + 'TaskDefinitionArn'], 'lastStatus': 'RUNNING',
                                   'containers': [{'name': role, 'lastStatus': 'RUNNING', 'imageDigest': outputs['ImageUri'].split('@')[1]}]}]}
            return {}
        with tempfile.TemporaryDirectory() as directory, patch.object(deploy, 'OUT', Path(directory)), patch.object(deploy, 'aws', side_effect=fake), patch.object(deploy, 'quiesce_candidate', return_value=['task/candidate']) as stop:
            record = {'status': 'blocked'}; deploy.rollback_candidate(snapshot, record)
            self.assertEqual(record['rollback']['status'], 'restored'); stop.assert_called_once()
            self.assertEqual(record['rollback']['outputs']['FileSystemId'], 'fs-retained')
            self.assertEqual(record['status'], 'blocked')
            self.assertEqual(calls[0][0:2], ('cloudformation', 'deploy'))
            self.assertIn('ImageUri=' + outputs['ImageUri'], calls[0])
            self.assertEqual(len(record['rollback']['runningTasks']), 3)

    def test_first_deploy_failure_leaves_candidate_stopped_without_deleting_data(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(deploy, 'OUT', Path(directory)), patch.object(deploy, 'aws') as aws, patch.object(deploy, 'quiesce_candidate', return_value=[]):
            record = {}; deploy.rollback_candidate(None, record)
            self.assertEqual(record['rollback']['status'], 'stopped_no_previous_deployment'); aws.assert_not_called()
            self.assertEqual(json.loads((Path(directory) / 'deployment-record.json').read_text()), record)

    def test_failed_restoration_quiesces_again_and_never_claims_success(self):
        for failure in [RuntimeError('restore_failed'), {'Stacks': [{'Outputs': []}]}]:
            with tempfile.TemporaryDirectory() as directory, patch.object(deploy, 'OUT', Path(directory)), patch.object(deploy, 'quiesce_candidate', return_value=[]) as stop:
                def fake(*args, **kwargs):
                    if isinstance(failure, Exception): raise failure
                    return failure
                with patch.object(deploy, 'aws', side_effect=fake):
                    record = {}; deploy.rollback_candidate(self.snapshot(), record)
                self.assertEqual(record['rollback']['status'], 'failed'); self.assertEqual(stop.call_count, 2)
                self.assertEqual(record['rollback']['stoppedAfterFailure'], [])

    def test_incomplete_inventory_cannot_prove_candidate_is_stopped(self):
        with patch.object(deploy, 'aws', side_effect=[{'taskArns': ['task/unknown']}, {'taskArns': []}, {'tasks': []}]):
            with self.assertRaisesRegex(ValueError, 'inventory_incomplete'): deploy.console_tasks()


if __name__ == '__main__': unittest.main()
