import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { JobStore } from '../src/store.js';
import { createServer } from '../src/server.js';
import { createRunner } from '../src/worker-runtime.js';
import { EcsCodex, stopExecution } from '../src/ecs-job.js';
import { createTaskTransport } from '../src/task-transport.js';
import { writePublicationRequest } from '../src/publication.js';
import { beginPublicationMerge, publicationDecision } from '../src/publication-decision.js';
import { PROOF_ROOT } from '../src/publication-policy.js';

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'console-review-recovery-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const config = { dataDir: root, transportDir: path.join(root, 'transport'), allowedScopes: [PROOF_ROOT], requiredChecks: ['engineering-console-publication-proof'], maxInstructionBytes: 100000, cluster: 'eng-console-runtime' };
  fs.mkdirSync(config.transportDir);
  const store = new JobStore(root);
  const job = store.create({ instruction: 'password=runtime-only write the proof', authorizedScope: [PROOF_ROOT] }, 'owner', 'a'.repeat(40));
  return { root, config, store, job };
}
async function listen(t, config, store, queue) {
  const server = createServer({ config, store, queue, authorizer: async () => ({ id: 'owner' }) });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  return `http://127.0.0.1:${server.address().port}`;
}

for (const mergeFirst of [false, true]) {
  test(`outbox visible before job state: cancellation arbitration with mergeFirst=${mergeFirst}`, async t => {
    const { config, store, job } = fixture(t);
    job.status = 'running'; store.save(job);
    job.changedFiles = [`${PROOF_ROOT}/race.md`];
    writePublicationRequest(config, job, 'harmless proof patch', new AbortController().signal);
    assert.equal(store.get(job.id).publicationState, undefined);
    if (mergeFirst) beginPublicationMerge(config.dataDir, job.id);
    let cancellations = 0;
    const base = await listen(t, config, store, { enqueue() {}, cancel() { cancellations++; } });
    const response = await fetch(`${base}/v1/engineering/${job.id}/cancel`, { method: 'POST', body: '{}' });
    assert.equal(response.status, mergeFirst ? 409 : 202);
    assert.equal(cancellations, mergeFirst ? 0 : 1);
    assert.equal(publicationDecision(config.dataDir, job.id), mergeFirst ? 'merge' : 'cancelled');
    if (!mergeFirst) assert.throws(() => beginPublicationMerge(config.dataDir, job.id), /publication_cancelled/);
  });
}

test('replacement controller records the exact recovered execution and checkpoint from shared storage', async t => {
  const { config, store, job } = fixture(t);
  job.status = 'running'; job.threadId = crypto.randomUUID();
  job.execution = { id: crypto.randomUUID(), taskArn: 'task/existing', eventOffset: 7 };
  store.save(job);
  const first = await listen(t, config, store, { enqueue() {} });
  const firstHealth = await (await fetch(`${first}/healthz`)).json();
  const queued = [];
  const replacement = new JobStore(config.dataDir);
  const second = await listen(t, config, replacement, { enqueue(id) { queued.push(id); } });
  const secondHealth = await (await fetch(`${second}/healthz`)).json();
  const recovered = replacement.get(job.id);
  assert.notEqual(firstHealth.instance, secondHealth.instance);
  assert.equal(recovered.controllerRecovery.instance, secondHealth.instance);
  assert.equal(recovered.controllerRecovery.taskArn, 'task/existing');
  assert.equal(recovered.controllerRecovery.executionId, job.execution.id);
  assert.equal(recovered.controllerRecovery.eventOffset, 7);
  assert.equal(recovered.controllerRecovery.threadId, job.threadId);
  assert.equal(recovered.instructionRecoverable, false);
  assert.deepEqual(queued, [job.id]);
});

test('cancelled worker discovers an already stopped execution whose RunTask response was lost', async t => {
  const { config, store, job } = fixture(t);
  const transport = createTaskTransport(config.transportDir, { instruction: 'password=private', archive: 'large input' });
  fs.writeFileSync(path.join(transport.directory, 'checkpoint.json'), '{}');
  job.cancelRequested = true; job.execution = { id: transport.id, taskArn: null };
  store.save(job);
  const operations = [];
  const callAws = async (_, operation, args) => {
    operations.push(operation);
    if (operation === 'list-tasks') { assert.equal(args.startedBy, undefined); return { taskArns: args.desiredStatus === 'STOPPED' ? ['task/recovered'] : [] }; }
    if (operation === 'stop-task') return {};
    assert.equal(operation, 'describe-tasks');
    return { tasks: [{ taskArn: 'task/recovered', startedBy: transport.id, taskDefinitionArn: job.execution.taskDefinitionArn, lastStatus: 'STOPPED' }] };
  };
  const run = createRunner(config, store, null, { executionStop: (cfg, candidate, db) => stopExecution(cfg, candidate, db, callAws, { pollMs: 1 }) });
  await run(job, new AbortController().signal);
  assert.deepEqual(operations, ['list-tasks', 'list-tasks', 'describe-tasks']);
  assert.equal(store.get(job.id).status, 'cancelled');
  assert.equal(fs.existsSync(path.join(transport.directory, 'input.json')), false);
  assert.equal(fs.existsSync(path.join(transport.directory, 'checkpoint.json')), true);
});

test('ECS execution checks cancellation before retrying a missing task ARN', async t => {
  const { config, store, job } = fixture(t);
  const transport = createTaskTransport(config.transportDir, {});
  const taskDefinitionArn = 'arn:aws:ecs:us-east-1:111111111111:task-definition/eng-console-job:1', image = `111111111111.dkr.ecr.us-east-1.amazonaws.com/eng-console-runtime@sha256:${'a'.repeat(64)}`;
  job.execution = { id: transport.id, taskDefinitionArn, image, taskArn: null, requestedAt: new Date().toISOString() }; store.save(job);
  Object.assign(config, { jobTaskDefinition: taskDefinitionArn, jobImage: image, jobSecurityGroup: 'sg', brokerUrl: 'https://console.example', model: 'test', jobSubnets: ['a', 'b'] });
  const definition = { taskDefinitionArn, family: 'eng-console-job', networkMode: 'awsvpc', requiresCompatibilities: ['FARGATE'], containerDefinitions: [{ name: 'job', image, readonlyRootFilesystem: true, user: '10001:10001', command: ['node', '/app/scripts/isolated-job.mjs'], entryPoint: ['/usr/bin/tini', '--'] }] };
  const operations = [];
  const callAws = async (_, operation) => {
    operations.push(operation);
    if (operation === 'describe-task-definition') {
      const latest = store.get(job.id); latest.cancelRequested = true; store.save(latest);
      return definition;
    }
    if (operation === 'list-tasks') return { taskArns: ['task/recovered'] };
    if (operation === 'stop-task') return {};
    assert.equal(operation, 'describe-tasks');
    return { tasks: [{ taskArn: 'task/recovered', startedBy: transport.id, taskDefinitionArn: job.execution.taskDefinitionArn, lastStatus: 'STOPPED' }] };
  };
  const codex = new EcsCodex({ config, store, job, callAws, workspace: '', pollMs: 1 });
  for await (const event of codex.run('proof', null, {}, new AbortController().signal)) assert.fail(event.type);
  assert.equal(operations.includes('run-task'), false);
  assert.ok(job.execution.stoppedAt);
});

test('definitive zero-task rejection leaves the failed job continuable without task discovery', async t => {
  const { root, config, store, job } = fixture(t);
  const previous = { id: crypto.randomUUID(), stoppedAt: new Date().toISOString() }; job.previousExecution = previous;
  config.repository = path.join(root, 'repo'); config.workspaceRoot = path.join(root, 'workspaces'); fs.mkdirSync(config.repository);
  const git = (...args) => execFileSync('git', ['-c', 'user.name=Test', '-c', 'user.email=test@example.com', ...args], { cwd: config.repository, encoding: 'utf8' }).trim();
  git('init', '-b', 'main'); git('commit', '--allow-empty', '-m', 'base'); job.startingRevision = git('rev-parse', 'HEAD');
  const taskDefinitionArn = 'arn:aws:ecs:us-east-1:111111111111:task-definition/eng-console-job:1', image = `111111111111.dkr.ecr.us-east-1.amazonaws.com/eng-console-runtime@sha256:${'a'.repeat(64)}`;
  const transport = createTaskTransport(config.transportDir, {});
  job.execution = { id: transport.id, taskDefinitionArn, image, taskArn: null, requestedAt: new Date().toISOString() }; store.save(job);
  Object.assign(config, { jobTaskDefinition: taskDefinitionArn, jobImage: image, jobSecurityGroup: 'sg', brokerUrl: 'https://console.example', model: 'test', jobSubnets: ['a', 'b'] });
  const definition = { taskDefinitionArn, family: 'eng-console-job', networkMode: 'awsvpc', requiresCompatibilities: ['FARGATE'], containerDefinitions: [{ name: 'job', image, readonlyRootFilesystem: true, user: '10001:10001', command: ['node', '/app/scripts/isolated-job.mjs'], entryPoint: ['/usr/bin/tini', '--'] }] };
  const operations = [];
  const codex = new EcsCodex({ config, store, job, workspace: path.join(config.workspaceRoot, job.id), callAws: async (_, operation) => {
    operations.push(operation);
    if (operation === 'describe-task-definition') return definition;
    assert.equal(operation, 'run-task'); return { tasks: [], failures: [{ reason: 'RESOURCE:FARGATE' }] };
  } });
  await createRunner(config, store, class { constructor() { return codex; } })(job, new AbortController().signal);
  assert.equal(store.get(job.id).status, 'failed');
  assert.equal(store.get(job.id).error, 'isolated_job_launch_rejected');
  assert.ok(store.get(job.id).execution.launchRejectedAt); assert.ok(store.get(job.id).execution.stoppedAt);
  assert.equal(fs.existsSync(path.join(transport.directory, 'input.json')), false);
  // Simulate a crash after the rejection marker/cleanup but before the outer
  // runner persisted failed. A replacement must not call ECS even once.
  const recovered = store.get(job.id); recovered.status = 'running'; store.save(recovered);
  const replacement = new EcsCodex({ config, store, job: recovered, workspace: path.join(config.workspaceRoot, job.id), callAws: async () => assert.fail('terminal rejection must not contact ECS') });
  await createRunner(config, store, class { constructor() { return replacement; } })(recovered, new AbortController().signal);
  assert.equal(store.get(job.id).status, 'failed'); assert.equal(store.get(job.id).error, 'isolated_job_launch_rejected');
  const queued = [], base = await listen(t, config, store, { enqueue(id) { queued.push(id); } });
  const response = await fetch(`${base}/v1/engineering/${job.id}/continue`, { method: 'POST', body: JSON.stringify({ instruction: 'Retry when capacity is available' }) });
  assert.equal(response.status, 202); assert.equal(store.get(job.id).execution, null); assert.deepEqual(queued, [job.id]);
  assert.deepEqual(store.get(job.id).previousExecution, previous);
  assert.deepEqual(operations, ['describe-task-definition', 'run-task']);
});
