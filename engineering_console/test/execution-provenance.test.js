import test from 'node:test';
import assert from 'node:assert/strict';
import { discoverExecutionTask, recordedRuntime, validateExecutionTask } from '../src/ecs-job.js';

const execution = { id: 'launch', taskArn: 'task/accepted', taskDefinitionArn: 'arn:aws:ecs:us-east-1:111111111111:task-definition/eng-console-job:1', image: `111111111111.dkr.ecr.us-east-1.amazonaws.com/eng-console-runtime@sha256:${'a'.repeat(64)}` };
const task = { taskArn: execution.taskArn, startedBy: execution.id, taskDefinitionArn: execution.taskDefinitionArn, lastStatus: 'RUNNING', containers: [{ name: 'job', imageDigest: execution.image.split('@')[1] }] };

test('recovered task must match its persisted launch, definition and immutable image', () => {
  assert.equal(validateExecutionTask(execution, task), task);
  for (const key of ['id', 'taskArn', 'taskDefinitionArn', 'image']) {
    assert.throws(() => validateExecutionTask({ ...execution, [key]: 'wrong' }, task));
  }
  assert.throws(() => recordedRuntime({}), /runtime_identity_missing/);
  assert.throws(() => recordedRuntime({ ...execution, image: 'runtime:latest' }), /runtime_identity_missing/);
  assert.throws(() => validateExecutionTask(execution, { ...task, containers: [] }), /image_unverified/);
  assert.throws(() => validateExecutionTask(execution, { ...task, containers: [{ ...task.containers[0], imageDigest: 'sha256:wrong' }] }), /image_unverified/);
  // ECS has no pulled image digest while provisioning; it must supply one
  // before a running checkpoint or a stopped result can be trusted.
  validateExecutionTask(execution, { ...task, lastStatus: 'PROVISIONING', containers: [] });
});

test('lost launch discovery inventories both statuses in complete batches and rejects duplicate executions', async () => {
  for (const duplicate of [false, true]) {
    const running = Array.from({ length: 101 }, (_, i) => `task/other${i}`);
    const batches = [], statuses = [];
    const found = discoverExecutionTask({ cluster: 'eng-console-runtime' }, execution, async (_, operation, args) => {
      if (operation === 'list-tasks') {
        assert.equal(args.startedBy, undefined); statuses.push(args.desiredStatus);
        return { taskArns: args.desiredStatus === 'RUNNING' ? running : [execution.taskArn] };
      }
      batches.push(args.tasks.length);
      return { tasks: args.tasks.map(arn => ({ ...task, taskArn: arn, lastStatus: 'STOPPED', startedBy: arn === execution.taskArn || duplicate && arn === running[0] ? execution.id : 'other' })) };
    });
    if (duplicate) await assert.rejects(found, /not_unique/);
    else assert.equal((await found).taskArn, execution.taskArn);
    assert.deepEqual(statuses, ['RUNNING', 'STOPPED']); assert.deepEqual(batches, [100, 2]);
  }
});

test('missing task descriptions cannot become evidence of a unique launch', async () => {
  await assert.rejects(discoverExecutionTask({}, execution, async (_, op) => op === 'list-tasks' ? { taskArns: [execution.taskArn, 'task/unknown'] } : { tasks: [task] }), /inventory_unverified/);
});
