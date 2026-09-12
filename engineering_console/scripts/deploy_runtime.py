"""Deploy only the isolated Console and require a real private-VPC proof."""
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
STATE_VERSION = 'isolated-console-v3'
SERVICES = ['eng-console-controller', 'eng-console-broker', 'eng-console-publisher']

def stack_outputs(stack):
    return {v['OutputKey']: v['OutputValue'] for v in stack.get('Outputs', [])}

def snapshot_previous(previous):
    if not previous: return None
    stack = previous['Stacks'][0]
    outputs = stack_outputs(stack)
    if outputs.get('RuntimeStateVersion') != STATE_VERSION:
        raise ValueError('previous_runtime_state_not_rollback_compatible')
    if stack.get('StackStatus') not in ['CREATE_COMPLETE', 'UPDATE_COMPLETE', 'UPDATE_ROLLBACK_COMPLETE']:
        raise ValueError('previous_stack_not_stable')
    if outputs.get('ClusterName') != STACK or not re.fullmatch(r'[0-9a-f]{40}', outputs.get('SourceSha', '')) or not re.search(r'@sha256:[0-9a-f]{64}$', outputs.get('ImageUri', '')):
        raise ValueError('previous_runtime_identity_missing')
    parameters = stack.get('Parameters', [])
    if not parameters or any(not isinstance(p.get('ParameterValue'), str) or p['ParameterValue'] == '****' for p in parameters):
        raise ValueError('previous_parameters_unavailable')
    response = aws('ecs', 'describe-services', '--cluster', STACK, '--services', *SERVICES)
    services = response.get('services', [])
    if response.get('failures') or len(services) != len(SERVICES) or {s.get('serviceName') for s in services} != set(SERVICES):
        raise ValueError('previous_services_unverified')
    counts = {s['serviceName']: s.get('desiredCount') for s in services}
    if any(type(n) is not int or n not in [0, 1] for n in counts.values()):
        raise ValueError('previous_service_count_invalid')
    template = aws('cloudformation', 'get-template', '--stack-name', STACK, '--template-stage', 'Original')['TemplateBody']
    if not isinstance(template, (str, dict)) or not template: raise ValueError('previous_template_unavailable')
    template_file = OUT / 'previous-template.yaml'
    template_file.write_text(template if isinstance(template, str) else json.dumps(template))
    snapshot = {'outputs': outputs, 'parameters': parameters, 'desiredCounts': counts, 'templateFile': str(template_file)}
    (OUT / 'previous-deployment.json').write_text(json.dumps(snapshot, indent=2) + '\n')
    return snapshot

def console_tasks():
    arns = set()
    for status in ['RUNNING', 'STOPPED']:
        response = aws('ecs', 'list-tasks', '--cluster', STACK, '--desired-status', status)
        if not isinstance(response.get('taskArns'), list): raise ValueError('rollback_inventory_incomplete')
        arns.update(response['taskArns'])
    tasks = []
    ordered = sorted(arns)
    for start in range(0, len(ordered), 100):
        batch = ordered[start:start + 100]
        response = aws('ecs', 'describe-tasks', '--cluster', STACK, '--tasks', *batch)
        rows = response.get('tasks', [])
        if response.get('failures') or len(rows) != len(batch) or {t.get('taskArn') for t in rows} != set(batch):
            raise ValueError('rollback_inventory_incomplete')
        tasks.extend(rows)
    return tasks

def quiesce_candidate():
    # Stop admission and both other shared-state writers before stopping jobs.
    # The fixed cluster and exact families exclude every non-Console workload.
    for name in SERVICES:
        aws('ecs', 'update-service', '--cluster', STACK, '--service', name, '--desired-count', '0')
    aws('ecs', 'wait', 'services-stable', '--cluster', STACK, '--services', *SERVICES)
    stopped, empty = set(), 0
    for attempt in range(30):
        active = [t for t in console_tasks() if t.get('lastStatus') != 'STOPPED']
        if not active:
            empty += 1
            if empty == 2: return sorted(stopped)
        else:
            empty = 0
            for task in active:
                if not re.fullmatch(r'arn:[^:]+:ecs:[^:]+:\d{12}:task-definition/eng-console-(controller|broker|publisher|job|probe):\d+', task.get('taskDefinitionArn', '')):
                    raise ValueError('rollback_cluster_contains_unknown_task')
                aws('ecs', 'stop-task', '--cluster', STACK, '--task', task['taskArn'], '--reason', 'Console candidate failed live verification')
                stopped.add(task['taskArn'])
            aws('ecs', 'wait', 'tasks-stopped', '--cluster', STACK, '--tasks', *[t['taskArn'] for t in active])
        time.sleep(2)
    raise ValueError('candidate_stop_unconfirmed')

def rollback_candidate(snapshot, record):
    evidence = record['rollback'] = {'status': 'stopping_candidate'}
    # Persist each boundary, including failed/partial rollback, for operators.
    def save(): (OUT / 'deployment-record.json').write_text(json.dumps(record, indent=2) + '\n')
    save()
    try:
        evidence['stoppedTasks'] = quiesce_candidate()
        evidence['status'] = 'candidate_stopped'; save()
        if snapshot is None:
            evidence['status'] = 'stopped_no_previous_deployment'; save(); return
        aws('cloudformation', 'deploy', '--stack-name', STACK, '--template-file', snapshot['templateFile'],
            '--parameter-overrides', *[p['ParameterKey'] + '=' + p['ParameterValue'] for p in snapshot['parameters']],
            '--no-fail-on-empty-changeset', text_output=True)
        outputs = stack_outputs(aws('cloudformation', 'describe-stacks', '--stack-name', STACK)['Stacks'][0])
        for key in ['ImageUri', 'SourceSha', 'FileSystemId', 'ClusterName', 'RuntimeStateVersion']:
            if not outputs.get(key) or outputs[key] != snapshot['outputs'].get(key): raise ValueError('rollback_identity_mismatch:' + key)
        for name, count in snapshot['desiredCounts'].items():
            aws('ecs', 'update-service', '--cluster', STACK, '--service', name, '--desired-count', str(count))
        aws('ecs', 'wait', 'services-stable', '--cluster', STACK, '--services', *SERVICES)
        running = []
        for name, count in snapshot['desiredCounts'].items():
            arns = aws('ecs', 'list-tasks', '--cluster', STACK, '--service-name', name, '--desired-status', 'RUNNING')['taskArns']
            if len(arns) != count: raise ValueError('rollback_service_count_mismatch')
            for arn in arns:
                role = name.removeprefix('eng-console-')
                task = observed_task(aws('ecs', 'describe-tasks', '--cluster', STACK, '--tasks', arn), arn, outputs[role.title() + 'TaskDefinitionArn'])
                require_task_evidence(task, role, outputs['ImageUri'].split('@')[1], 'RUNNING')
                running.append(task['taskArn'])
        evidence.update(status='restored', outputs=outputs, runningTasks=running); save()
    except Exception as error:
        evidence.update(status='failed', reason=str(error) if isinstance(error, (ValueError, RuntimeError)) else type(error).__name__)
        # Restoration can partially start services; quiesce them on any failure.
        try: evidence['stoppedAfterFailure'] = quiesce_candidate()
        except Exception: evidence['stopAfterFailure'] = 'unconfirmed'
        save()

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
        raise ValueError('trusted_services_require_distinct_task_roles')

def observed_task(result, arn, definition):
    tasks = result.get('tasks') if isinstance(result, dict) else None
    if result.get('failures') or not isinstance(tasks, list) or len(tasks) != 1:
        raise ValueError('task_observation_incomplete')
    task = tasks[0]
    if not isinstance(task, dict) or task.get('taskArn') != arn or task.get('taskDefinitionArn') != definition or not task.get('lastStatus'):
        raise ValueError('task_observation_identity_mismatch')
    return task

def require_task_evidence(task, name, image_digest, status, successful_exit=True):
    containers = task.get('containers') if isinstance(task, dict) else None
    if task.get('lastStatus') != status or not isinstance(containers, list) or len(containers) != 1:
        raise ValueError('task_container_evidence_incomplete')
    container = containers[0]
    if not isinstance(container, dict) or container.get('name') != name or container.get('imageDigest') != image_digest or container.get('lastStatus') != status:
        raise ValueError('task_container_evidence_mismatch')
    if status == 'STOPPED' and successful_exit and container.get('exitCode') != 0:
        raise ValueError('private_end_to_end_proof_failed')
    return container

def probe_events(outputs, arn):
    return probe_log_values(outputs['ProbeLogGroupName'], 'probe/probe/' + arn.rsplit('/', 1)[1])

def probe_log_values(group, stream):
    try:
        events = aws('logs', 'get-log-events', '--log-group-name', group, '--log-stream-name', stream)['events']
    except RuntimeError: return []  # The stream may not exist yet.
    values = []
    for event in events:
        try: value = json.loads(event['message'])
        except (KeyError, TypeError, ValueError): continue
        if isinstance(value, dict): values.append(value)
    return values

def select_recovery_ready(values, source):
    matches = [v for v in values if v.get('phase') == 'recovery_ready' and v.get('source') == source]
    if len(matches) > 1: raise ValueError('recovery_probe_ready_ambiguous')
    if not matches: return None
    value = matches[0]
    if any(not UUID.fullmatch(str(value.get(key) or '')) for key in ['jobId', 'executionId', 'controllerInstance']):
        raise ValueError('recovery_probe_ready_invalid')
    if not str(value.get('executionTask') or '').startswith('arn:aws') or not value.get('threadId') or type(value.get('eventOffset')) is not int or value['eventOffset'] <= 0:
        raise ValueError('recovery_probe_checkpoint_invalid')
    return value

def require_single_execution_task(task_arns, expected):
    if set(task_arns) != {expected}: raise ValueError('duplicate_or_mismatched_recovery_execution')

def restart_controller(outputs, marker, initial, digest):
    cluster = outputs['ClusterName']
    for key in ['jobId', 'executionId', 'controllerInstance']:
        if not re.fullmatch(r'[0-9a-f-]{36}', str(marker.get(key, ''))): raise ValueError('recovery_marker_identity_invalid')
    if not marker.get('threadId') or type(marker.get('eventOffset')) is not int or marker['eventOffset'] <= 0:
        raise ValueError('recovery_requires_durable_checkpoint')
    execution = observed_task(aws('ecs', 'describe-tasks', '--cluster', cluster, '--tasks', marker['executionTask']),
                              marker['executionTask'], outputs['JobTaskDefinitionArn'])
    require_task_evidence(execution, 'job', digest, 'RUNNING')
    if execution.get('startedBy') != marker['executionId']: raise ValueError('recovery_execution_identity_mismatch')
    old = observed_task(aws('ecs', 'describe-tasks', '--cluster', cluster, '--tasks', initial['taskArn']),
                        initial['taskArn'], outputs['ControllerTaskDefinitionArn'])
    require_task_evidence(old, 'controller', digest, 'RUNNING')
    aws('ecs', 'stop-task', '--cluster', cluster, '--task', old['taskArn'], '--reason', 'Console active-job recovery verification')
    aws('ecs', 'wait', 'tasks-stopped', '--cluster', cluster, '--tasks', old['taskArn'])
    stopped = observed_task(aws('ecs', 'describe-tasks', '--cluster', cluster, '--tasks', old['taskArn']),
                            old['taskArn'], outputs['ControllerTaskDefinitionArn'])
    require_task_evidence(stopped, 'controller', digest, 'STOPPED', successful_exit=False)
    aws('ecs', 'wait', 'services-stable', '--cluster', cluster, '--services', 'eng-console-controller')
    arns = aws('ecs', 'list-tasks', '--cluster', cluster, '--service-name', 'eng-console-controller', '--desired-status', 'RUNNING')['taskArns']
    if len(arns) != 1 or arns[0] == old['taskArn']: raise ValueError('replacement_controller_not_observed')
    new = observed_task(aws('ecs', 'describe-tasks', '--cluster', cluster, '--tasks', arns[0]), arns[0], outputs['ControllerTaskDefinitionArn'])
    require_task_evidence(new, 'controller', digest, 'RUNNING')
    return {'oldControllerTask': old['taskArn'], 'newControllerTask': new['taskArn'], 'executionTask': execution['taskArn'],
            'taskDefinition': new['taskDefinitionArn'], 'imageDigest': digest, 'activeAtRestart': True}

def verify_recovery_proof(proof, marker, restart):
    if not marker or not restart or restart.get('activeAtRestart') is not True: raise ValueError('active_job_recovery_not_exercised')
    recovery = proof.get('recovery') or {}
    if (proof.get('jobId') != marker['jobId'] or proof.get('executionTask') != marker['executionTask'] or
        recovery.get('executionId') != marker['executionId'] or recovery.get('threadId') != marker['threadId'] or
        recovery.get('previousInstance') != marker['controllerInstance'] or not recovery.get('instance') or
        recovery['instance'] == marker['controllerInstance'] or type(recovery.get('eventOffset')) is not int or
        type(recovery.get('checkpointOffset')) is not int or recovery['checkpointOffset'] < marker['eventOffset'] or
        recovery['eventOffset'] < recovery['checkpointOffset'] or restart.get('executionTask') != marker['executionTask'] or
        restart['oldControllerTask'] == restart['newControllerTask']):
        raise ValueError('active_job_recovery_proof_mismatch')

def verify_single_execution(outputs, marker, digest):
    cluster = outputs['ClusterName']
    arns = set()
    for status in ['RUNNING', 'STOPPED']:
        arns.update(aws('ecs', 'list-tasks', '--cluster', cluster, '--desired-status', status)['taskArns'])
    arns.add(marker['executionTask'])
    tasks = []
    ordered = sorted(arns)
    for start in range(0, len(ordered), 100):
        result = aws('ecs', 'describe-tasks', '--cluster', cluster, '--tasks', *ordered[start:start + 100])
        if result.get('failures') or len(result.get('tasks', [])) != len(ordered[start:start + 100]):
            raise ValueError('execution_inventory_incomplete')
        tasks.extend(t for t in result['tasks'] if t.get('startedBy') == marker['executionId'])
    require_single_execution_task([t['taskArn'] for t in tasks], marker['executionTask'])
    if len(tasks) != 1 or tasks[0]['taskArn'] != marker['executionTask'] or tasks[0]['taskDefinitionArn'] != outputs['JobTaskDefinitionArn']:
        raise ValueError('duplicate_or_mismatched_recovery_execution')
    require_task_evidence(tasks[0], 'job', digest, 'STOPPED')
    return [t['taskArn'] for t in tasks]

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
    deployed = False
    snapshot = None
    try:
        cfg, identity, previous = preflight()
        snapshot = snapshot_previous(previous)
        if args.preflight_only:
            print('Isolated Console configuration preflight passed'); return
        if not re.fullmatch(r'[0-9a-f]{40}', args.source or ''): raise ValueError('source_sha_required')
        pattern = re.escape(identity['Account']) + r'\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com/eng-console-[a-z0-9._/-]+@sha256:[0-9a-f]{64}'
        if not re.fullmatch(pattern, args.image or ''): raise ValueError('verified_console_image_required')
        record.update(source=args.source, image=args.image, previousOutputs=(previous or {}).get('Stacks', [{}])[0].get('Outputs', []), account=identity['Account'])
        parameters = [{'ParameterKey': k, 'ParameterValue': v} for k,v in {**cfg,'ImageUri':args.image,'SourceSha':args.source}.items()]
        parameter_file = OUT / 'parameters.json'; parameter_file.write_text(json.dumps(parameters))
        aws('cloudformation', 'deploy', '--stack-name', STACK, '--template-file', TEMPLATE, '--parameter-overrides', *[p['ParameterKey'] + '=' + p['ParameterValue'] for p in parameters], '--no-fail-on-empty-changeset', text_output=True)
        deployed = True
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
            require_task_evidence(task, role.lower(), args.image.split('@')[1], 'RUNNING')
            running.append({'taskArn':task['taskArn'],'taskDefinitionArn':task['taskDefinitionArn'],'imageDigest':task['containers'][0]['imageDigest']})
        record['runningTasks'] = running
        probe = aws('ecs','run-task','--cluster',cluster,'--task-definition',outputs['ProbeTaskDefinitionArn'],'--launch-type','FARGATE','--platform-version','1.4.0','--network-configuration',json.dumps({'awsvpcConfiguration':{'subnets':cfg['PrivateSubnetIds'].split(','),'securityGroups':[cfg['TrustedSecurityGroupId']],'assignPublicIp':'DISABLED'}}))
        if probe.get('failures') or len(probe.get('tasks',[])) != 1: raise ValueError('private_probe_launch_failed')
        arn = probe['tasks'][0]['taskArn']; record['probeTaskArn'] = arn
        marker = restart = None
        deadline = time.monotonic() + 2100
        ready_deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            task = observed_task(aws('ecs','describe-tasks','--cluster',cluster,'--tasks',arn), arn, outputs['ProbeTaskDefinitionArn'])
            ready = select_recovery_ready(probe_events(outputs, arn), args.source)
            if ready and marker is None:
                require_task_evidence(task, 'probe', args.image.split('@')[1], 'RUNNING')
                marker = ready
                record['recoveryRequest'] = marker
                (OUT / 'deployment-record.json').write_text(json.dumps(record, indent=2) + '\n')
                restart = restart_controller(outputs, marker, running[0], args.image.split('@')[1])
                record['controllerRestart'] = restart
                record['runningTasks'][0] = {'taskArn': restart['newControllerTask'], 'taskDefinitionArn': restart['taskDefinition'], 'imageDigest': restart['imageDigest']}
                (OUT / 'deployment-record.json').write_text(json.dumps(record, indent=2) + '\n')
            if marker is None and time.monotonic() >= ready_deadline:
                aws('ecs','stop-task','--cluster',cluster,'--task',arn,'--reason','Console recovery readiness timed out')
                raise ValueError('active_recovery_evidence_missing')
            if task['lastStatus'] == 'STOPPED': break
            time.sleep(10)
        else:
            aws('ecs','stop-task','--cluster',cluster,'--task',arn,'--reason','Console deployment proof timed out')
            raise ValueError('private_probe_timeout')
        require_task_evidence(task, 'probe', args.image.split('@')[1], 'STOPPED')
        proofs=[]
        for attempt in range(12):
            proofs = [v for v in probe_events(outputs, arn) if v.get('verified') is True and v.get('source') == args.source]
            if proofs: break
            time.sleep(5)
        if len(proofs) != 1: raise ValueError('durable_proof_record_missing')
        verify_recovery_proof(proofs[0], marker, restart)
        record['recoveryExecutionTasks'] = verify_single_execution(outputs, marker, args.image.split('@')[1])
        record.update(status='verified',proof=proofs[0],verifiedAt=datetime.datetime.now(datetime.timezone.utc).isoformat())
        print(json.dumps({'status':'verified','source':args.source,'endpoint':outputs['ConsoleUrl'],'proof':proofs[0]}))
    except Exception as error:
        record['reason'] = str(error) if isinstance(error,(ValueError,RuntimeError)) else type(error).__name__
        if deployed: rollback_candidate(snapshot, record)
        print('Console deployment blocked: ' + record['reason']); raise SystemExit(1)
    finally:
        if not args.preflight_only: (OUT/'deployment-record.json').write_text(json.dumps(record,indent=2)+'\n')

if __name__ == '__main__': main()
