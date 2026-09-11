"""Deploy only the isolated Console and require real private-VPC/recovery proof."""
import argparse
import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import time

STACK = 'eng-console-runtime'
TEMPLATE = 'engineering_console/deploy/template.yaml'
OUT = Path('runtime_deploy')
TRUSTED_TASK_ROLE_KEYS = ('ControllerTaskRoleArn', 'BrokerTaskRoleArn', 'PublisherTaskRoleArn')
UUID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$', re.I)

def aws(*args, allow_missing=False, text_output=False):
    result = subprocess.run(['aws', *args, '--output', 'json'], capture_output=True, text=True, env={**os.environ, 'AWS_PAGER': ''})
    if result.returncode:
        if allow_missing and 'does not exist' in result.stderr: return None
        raise RuntimeError('aws_' + '_'.join(args[:2]) + '_failed')
    if text_output: return result.stdout
    return json.loads(result.stdout) if result.stdout.strip() else {}

def validate_job_egress(group):
    if group.get('IpPermissions'): raise ValueError('job_security_group_must_have_no_ingress')
    for rule in group.get('IpPermissionsEgress', []):
        if rule.get('IpProtocol') != 'tcp' or rule.get('FromPort') != 443 or rule.get('ToPort') != 443:
            raise ValueError('job_egress_must_be_https_only')
        if rule.get('IpRanges') or rule.get('Ipv6Ranges') or not (rule.get('UserIdGroupPairs') or rule.get('PrefixListIds')):
            raise ValueError('job_egress_must_target_approved_security_groups_or_prefix_lists')
    if not group.get('IpPermissionsEgress'): raise ValueError('job_requires_private_broker_and_ecr_endpoints')

def validate_restore_targets(targets, subnets, reuse, managed):
    if targets and ((not reuse and not managed) or {t['SubnetId'] for t in targets} != set(subnets) or any(t['LifeCycleState'] != 'available' for t in targets)):
        raise ValueError('restore_mount_targets_require_explicit_complete_reuse')
    if reuse and not targets: raise ValueError('restore_mount_targets_missing')

def validate_trusted_task_roles(cfg):
    roles = [cfg.get(name) for name in TRUSTED_TASK_ROLE_KEYS]
    if any(not isinstance(role, str) or not role for role in roles) or len(set(roles)) != len(roles):
        raise ValueError('trusted_task_roles_must_be_distinct')

def observed_task(result, arn, definition):
    tasks = result.get('tasks') if isinstance(result, dict) else None
    if result.get('failures') or not isinstance(tasks, list) or len(tasks) != 1:
        raise ValueError('task_observation_incomplete')
    task = tasks[0]
    if not isinstance(task, dict) or task.get('taskArn') != arn or task.get('taskDefinitionArn') != definition or not task.get('lastStatus'):
        raise ValueError('task_observation_identity_mismatch')
    return task

def require_task_evidence(task, name, image_digest, status):
    containers = task.get('containers') if isinstance(task, dict) else None
    if task.get('lastStatus') != status or not isinstance(containers, list) or len(containers) != 1:
        raise ValueError('task_container_evidence_incomplete')
    container = containers[0]
    if not isinstance(container, dict) or container.get('name') != name or container.get('imageDigest') != image_digest or container.get('lastStatus') != status:
        raise ValueError('task_container_evidence_mismatch')
    if status == 'STOPPED' and container.get('exitCode') != 0:
        raise ValueError('private_end_to_end_proof_failed')
    return container

def probe_log_values(group, stream):
    try:
        events = aws('logs', 'get-log-events', '--log-group-name', group, '--log-stream-name', stream)['events']
    except RuntimeError:
        return []
    values = []
    for event in events:
        try: value = json.loads(event['message'])
        except (KeyError, TypeError, ValueError): continue
        if isinstance(value, dict): values.append(value)
    return values

def select_recovery_ready(values, source):
    matches = [value for value in values if value.get('recoveryReady') is True and value.get('source') == source]
    if len(matches) > 1: raise ValueError('recovery_probe_ready_ambiguous')
    if not matches: return None
    value = matches[0]
    if not UUID.fullmatch(str(value.get('jobId') or '')) or not UUID.fullmatch(str(value.get('executionId') or '')) or not str(value.get('executionTask') or '').startswith('arn:aws'):
        raise ValueError('recovery_probe_ready_invalid')
    return value

def require_single_execution_task(task_arns, expected):
    if set(task_arns) != {expected}: raise ValueError('recovery_execution_task_not_unique')

def preflight():
    raw = os.environ.get('ENG_CONSOLE_PARAMETERS_JSON', '')
    if not raw: raise ValueError('missing_protected_environment_ENG_CONSOLE_PARAMETERS_JSON')
    cfg = json.loads(raw)
    schema = aws('cloudformation', 'validate-template', '--template-body', 'file://' + TEMPLATE)['Parameters']
    keys = {p['ParameterKey'] for p in schema}
    required = {p['ParameterKey'] for p in schema if 'DefaultValue' not in p} - {'ImageUri', 'SourceSha'}
    missing = sorted(k for k in required if not cfg.get(k))
    if missing: raise ValueError('missing_console_parameters:' + ','.join(missing))
    if set(cfg) - keys or {'ImageUri', 'SourceSha'} & set(cfg): raise ValueError('unexpected_or_image_override_parameter')
    identity = aws('sts', 'get-caller-identity')
    for key, value in cfg.items():
        if not isinstance(value, str): raise ValueError('parameters_must_be_strings')
        if key.endswith('Arn') and (not value.startswith('arn:aws') or value.split(':')[4] != identity['Account']): raise ValueError('cross_account_parameter:' + key)
    validate_trusted_task_roles(cfg)
    subnets = cfg['PrivateSubnetIds'].split(',')
    if len(subnets) != 2 or len(set(subnets)) != 2: raise ValueError('two_private_subnets_required')
    rows = aws('ec2', 'describe-subnets', '--subnet-ids', *subnets)['Subnets']
    if len(rows) != 2 or len({r['AvailabilityZone'] for r in rows}) != 2 or any(r['VpcId'] != cfg['VpcId'] or r['MapPublicIpOnLaunch'] for r in rows): raise ValueError('private_subnet_configuration_invalid')
    group = aws('ec2', 'describe-security-groups', '--group-ids', cfg['JobSecurityGroupId'])['SecurityGroups'][0]
    if group['VpcId'] != cfg['VpcId']: raise ValueError('job_security_group_vpc_mismatch')
    validate_job_egress(group)
    previous = aws('cloudformation', 'describe-stacks', '--stack-name', STACK, allow_missing=True)
    previous_parameters = {p['ParameterKey']: p['ParameterValue'] for p in previous['Stacks'][0].get('Parameters', [])} if previous else {}
    if previous:
        for name, default in [('ExistingFileSystemId', ''), ('ExistingMountTargetsPresent', 'false')]:
            if cfg.get(name, default) != previous_parameters.get(name, default):
                raise ValueError('existing_stack_storage_mode_must_be_preserved:' + name)
    existing = cfg.get('ExistingFileSystemId')
    if existing:
        tags = aws('efs', 'list-tags-for-resource', '--resource-id', existing)['Tags']
        if not any(t['Key'] == 'Name' and t['Value'] == 'eng-console-state' for t in tags): raise ValueError('restore_requires_retained_console_filesystem')
        targets = aws('efs', 'describe-mount-targets', '--file-system-id', existing)['MountTargets']
        # A deleted stack retains data but removes its mount targets. Recreate
        # them when absent; explicitly reuse a complete external pair otherwise.
        reuse = cfg.get('ExistingMountTargetsPresent', 'false') == 'true'
        managed = bool(previous) and previous_parameters.get('ExistingMountTargetsPresent', 'false') == 'false'
        validate_restore_targets(targets, subnets, reuse, managed)
    elif previous is None:
        if cfg.get('ExistingMountTargetsPresent', 'false') != 'false': raise ValueError('new_filesystem_requires_mount_targets')
        for filesystem in aws('efs', 'describe-file-systems')['FileSystems']:
            if any(t['Key'] == 'Name' and t['Value'] == 'eng-console-state' for t in filesystem.get('Tags', [])):
                raise ValueError('retained_console_filesystem_requires_explicit_reattachment')
    return cfg, identity, previous

def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--preflight-only', action='store_true'); parser.add_argument('--image'); parser.add_argument('--source'); args = parser.parse_args()
    OUT.mkdir(exist_ok=True)
    record = {'status': 'blocked', 'stack': STACK, 'startedAt': datetime.datetime.now(datetime.timezone.utc).isoformat()}
    try:
        cfg, identity, previous = preflight()
        if args.preflight_only:
            print('Isolated Console configuration preflight passed'); return
        if not re.fullmatch(r'[0-9a-f]{40}', args.source or ''): raise ValueError('source_sha_required')
        pattern = re.escape(identity['Account']) + r'\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com/eng-console-[a-z0-9._/-]+@sha256:[0-9a-f]{64}'
        if not re.fullmatch(pattern, args.image or ''): raise ValueError('verified_console_image_required')
        image_digest = args.image.split('@')[1]
        record.update(source=args.source, image=args.image, previousOutputs=(previous or {}).get('Stacks', [{}])[0].get('Outputs', []), account=identity['Account'])
        parameters = [{'ParameterKey': k, 'ParameterValue': v} for k,v in {**cfg,'ImageUri':args.image,'SourceSha':args.source}.items()]
        parameter_file = OUT / 'parameters.json'; parameter_file.write_text(json.dumps(parameters))
        aws('cloudformation', 'deploy', '--stack-name', STACK, '--template-file', TEMPLATE, '--parameter-overrides', *[p['ParameterKey'] + '=' + p['ParameterValue'] for p in parameters], '--no-fail-on-empty-changeset', text_output=True)
        stack = aws('cloudformation', 'describe-stacks', '--stack-name', STACK)['Stacks'][0]
        outputs = {v['OutputKey']:v['OutputValue'] for v in stack['Outputs']}; record['outputs'] = outputs
        cluster = outputs['ClusterName']; names = ['eng-console-controller','eng-console-broker','eng-console-publisher']
        aws('ecs', 'wait', 'services-stable', '--cluster', cluster, '--services', *names)
        running = []
        for name in names:
            arns = aws('ecs','list-tasks','--cluster',cluster,'--service-name',name,'--desired-status','RUNNING')['taskArns']
            if len(arns) != 1: raise ValueError('singleton_runtime_count_invalid')
            role = name.removeprefix('eng-console-').title()
            task = observed_task(aws('ecs','describe-tasks','--cluster',cluster,'--tasks',*arns), arns[0], outputs[role + 'TaskDefinitionArn'])
            require_task_evidence(task, role.lower(), image_digest, 'RUNNING')
            running.append({'service':name,'taskArn':task['taskArn'],'taskDefinitionArn':task['taskDefinitionArn'],'imageDigest':task['containers'][0]['imageDigest']})
        record['runningTasks'] = running
        controller_before = next(item for item in running if item['service'] == 'eng-console-controller')

        probe = aws('ecs','run-task','--cluster',cluster,'--task-definition',outputs['ProbeTaskDefinitionArn'],'--launch-type','FARGATE','--platform-version','1.4.0','--network-configuration',json.dumps({'awsvpcConfiguration':{'subnets':cfg['PrivateSubnetIds'].split(','),'securityGroups':[cfg['TrustedSecurityGroupId']],'assignPublicIp':'DISABLED'}}))
        if probe.get('failures') or len(probe.get('tasks',[])) != 1: raise ValueError('private_probe_launch_failed')
        arn = probe['tasks'][0]['taskArn']; record['probeTaskArn'] = arn
        stream = 'probe/probe/' + arn.rsplit('/', 1)[1]
        deadline = time.monotonic() + 2100

        recovery = None
        while time.monotonic() < min(deadline, time.monotonic() + 600):
            task = observed_task(aws('ecs','describe-tasks','--cluster',cluster,'--tasks',arn), arn, outputs['ProbeTaskDefinitionArn'])
            if task['lastStatus'] == 'STOPPED':
                require_task_evidence(task, 'probe', image_digest, 'STOPPED')
                raise ValueError('probe_stopped_before_active_recovery_evidence')
            recovery = select_recovery_ready(probe_log_values(outputs['ProbeLogGroupName'], stream), args.source)
            if recovery: break
            time.sleep(2)
        if not recovery:
            aws('ecs','stop-task','--cluster',cluster,'--task',arn,'--reason','Console recovery proof never observed an active coding task')
            raise ValueError('active_recovery_evidence_missing')

        execution_task = observed_task(
            aws('ecs','describe-tasks','--cluster',cluster,'--tasks',recovery['executionTask']),
            recovery['executionTask'], outputs['JobTaskDefinitionArn'])
        if execution_task.get('startedBy') != recovery['executionId']:
            raise ValueError('recovery_execution_started_by_mismatch')
        require_task_evidence(execution_task, 'job', image_digest, 'RUNNING')

        # Interrupt the controller while a real Codex task is active. The ECS
        # service must replace the controller and recover the same durable job /
        # execution without launching a duplicate coding task or publication.
        aws('ecs','stop-task','--cluster',cluster,'--task',controller_before['taskArn'],'--reason','Engineering Console deployment recovery verification')
        aws('ecs','wait','services-stable','--cluster',cluster,'--services','eng-console-controller')
        replacement_arns = aws('ecs','list-tasks','--cluster',cluster,'--service-name','eng-console-controller','--desired-status','RUNNING')['taskArns']
        if len(replacement_arns) != 1 or replacement_arns[0] == controller_before['taskArn']:
            raise ValueError('controller_recovery_replacement_missing')
        controller_after = observed_task(
            aws('ecs','describe-tasks','--cluster',cluster,'--tasks',replacement_arns[0]),
            replacement_arns[0], outputs['ControllerTaskDefinitionArn'])
        require_task_evidence(controller_after, 'controller', image_digest, 'RUNNING')
        record['controllerRecovery'] = {
            'jobId': recovery['jobId'], 'executionId': recovery['executionId'], 'executionTask': recovery['executionTask'],
            'controllerBefore': controller_before['taskArn'], 'controllerAfter': controller_after['taskArn']
        }

        while time.monotonic() < deadline:
            task = observed_task(aws('ecs','describe-tasks','--cluster',cluster,'--tasks',arn), arn, outputs['ProbeTaskDefinitionArn'])
            if task['lastStatus'] == 'STOPPED': break
            time.sleep(10)
        else:
            aws('ecs','stop-task','--cluster',cluster,'--task',arn,'--reason','Console deployment proof timed out')
            raise ValueError('private_probe_timeout')
        require_task_evidence(task, 'probe', image_digest, 'STOPPED')

        values = probe_log_values(outputs['ProbeLogGroupName'], stream)
        proofs = [value for value in values if value.get('verified') is True and value.get('source') == args.source]
        if len(proofs) != 1: raise ValueError('durable_proof_record_missing')
        proof = proofs[0]
        if proof.get('jobId') != recovery['jobId'] or proof.get('executionId') != recovery['executionId'] or proof.get('executionTask') != recovery['executionTask']:
            raise ValueError('recovery_proof_execution_identity_changed')

        execution_arns = []
        for desired in ('RUNNING', 'STOPPED'):
            observed = aws('ecs','list-tasks','--cluster',cluster,'--started-by',recovery['executionId'],'--desired-status',desired)
            execution_arns.extend(observed.get('taskArns', []))
        require_single_execution_task(execution_arns, recovery['executionTask'])

        record.update(status='verified',proof=proof,verifiedAt=datetime.datetime.now(datetime.timezone.utc).isoformat())
        print(json.dumps({'status':'verified','source':args.source,'endpoint':outputs['ConsoleUrl'],'proof':proof,'recovery':record['controllerRecovery']}))
    except Exception as error:
        record['reason'] = str(error) if isinstance(error,(ValueError,RuntimeError)) else type(error).__name__
        print('Console deployment blocked: ' + record['reason']); raise SystemExit(1)
    finally:
        if not args.preflight_only: (OUT/'deployment-record.json').write_text(json.dumps(record,indent=2)+'\n')

if __name__ == '__main__': main()
