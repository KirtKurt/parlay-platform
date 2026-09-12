import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import { JobStore } from '../src/store.js';
import { createServer } from '../src/server.js';
import { PROOF_ROOT } from '../src/publication-policy.js';
import { publicationDecision, beginPublicationMerge } from '../src/publication-decision.js';
import { shouldStopCancelledExecution } from '../src/worker-runtime.js';
import { scrubTerminalTransport } from '../src/ecs-job.js';

function fixture(t) {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'inqsi-restart-safety-'));
  t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
  return dataDir;
}

function config(dataDir) {
  return {
    dataDir,
    allowedScopes: [PROOF_ROOT],
    maxConcurrentJobs: 1,
    maxInstructionBytes: 100_000
  };
}

function passiveQueue(enqueued = []) {
  return {
    enqueue(id) { enqueued.push(id); return true; },
    cancel() { return true; }
  };
}

async function postCancel(server, id) {
  const address = server.address();
  return new Promise((resolve, reject) => {
    const request = http.request({
      hostname: '127.0.0.1', port: address.port,
      path: `/v1/engineering/${id}/cancel`, method: 'POST',
      headers: { 'content-type': 'application/json' }
    }, (res) => {
      let body = '';
      res.on('data', (chunk) => { body += chunk; });
      res.on('end', () => resolve({ status: res.statusCode, body: JSON.parse(body) }));
    });
    request.on('error', reject);
    request.end('{}');
  });
}

test('redacted runtime instruction is marked nonrecoverable and never replayed after restart', (t) => {
  const dataDir = fixture(t);
  const initial = new JobStore(dataDir);
  const literal = 'password=fixture-value write the bounded proof';
  const job = initial.create({ instruction: literal, authorizedScope: [PROOF_ROOT] }, 'owner', 'a'.repeat(40));
  job.status = 'running';
  initial.save(job);

  assert.equal(job.instruction, literal, 'live process retains the authorized literal instruction');
  const durable = new JobStore(dataDir).get(job.id);
  assert.equal(durable.instruction.includes('fixture-value'), false, 'durable state remains redacted');
  assert.equal(durable.instructionRecoverable, false);

  const recoveredStore = new JobStore(dataDir);
  const enqueued = [];
  const server = createServer({ config: config(dataDir), authorizer: async () => ({ id: 'owner' }), store: recoveredStore, queue: passiveQueue(enqueued) });
  t.after(() => server.close());

  const recovered = recoveredStore.get(job.id);
  assert.equal(recovered.status, 'blocked');
  assert.equal(recovered.error, 'runtime_instruction_unavailable_after_restart');
  assert.deepEqual(enqueued, []);
});

test('legacy redacted durable instruction without recovery marker also fails closed', (t) => {
  const dataDir = fixture(t);
  const initial = new JobStore(dataDir);
  const job = initial.create({ instruction: 'password=fixture-value write the bounded proof', authorizedScope: [PROOF_ROOT] }, 'owner', 'a'.repeat(40));
  job.status = 'running';
  initial.save(job);

  const target = initial.file(job.id);
  const legacy = JSON.parse(fs.readFileSync(target, 'utf8'));
  assert.match(legacy.instruction, /\[REDACTED\]/);
  delete legacy.instructionRecoverable;
  fs.writeFileSync(target, `${JSON.stringify(legacy, null, 2)}\n`, { mode: 0o600 });

  const recoveredStore = new JobStore(dataDir);
  const enqueued = [];
  const server = createServer({ config: config(dataDir), authorizer: async () => ({ id: 'owner' }), store: recoveredStore, queue: passiveQueue(enqueued) });
  t.after(() => server.close());

  const recovered = recoveredStore.get(job.id);
  assert.equal(recovered.status, 'blocked');
  assert.equal(recovered.error, 'runtime_instruction_unavailable_after_restart');
  assert.deepEqual(enqueued, []);
});

test('unchanged durable instruction remains restart-recoverable', (t) => {
  const dataDir = fixture(t);
  const initial = new JobStore(dataDir);
  const job = initial.create({ instruction: 'write the bounded proof', authorizedScope: [PROOF_ROOT] }, 'owner', 'a'.repeat(40));
  job.status = 'running';
  initial.save(job);
  assert.equal(new JobStore(dataDir).get(job.id).instructionRecoverable, true);

  const recoveredStore = new JobStore(dataDir);
  const enqueued = [];
  const server = createServer({ config: config(dataDir), authorizer: async () => ({ id: 'owner' }), store: recoveredStore, queue: passiveQueue(enqueued) });
  t.after(() => server.close());

  assert.equal(recoveredStore.get(job.id).status, 'queued');
  assert.deepEqual(enqueued, [job.id]);
});

test('accepted publication cancellation stays pending until trusted publisher confirms GitHub outcome', async (t) => {
  const dataDir = fixture(t);
  const store = new JobStore(dataDir);
  const job = store.create({ instruction: 'write proof', authorizedScope: [PROOF_ROOT] }, 'owner', 'a'.repeat(40));
  job.status = 'published';
  job.publicationState = 'checks_pending';
  job.pullRequest = 'https://github.com/KirtKurt/parlay-platform/pull/1';
  store.save(job);

  const cancelled = [];
  const queue = { enqueue() { return true; }, cancel(id) { cancelled.push(id); return true; } };
  const server = createServer({ config: config(dataDir), authorizer: async () => ({ id: 'owner' }), store, queue });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  t.after(() => server.close());

  const response = await postCancel(server, job.id);

  assert.equal(response.status, 202);
  assert.deepEqual(cancelled, [job.id]);
  const pending = store.get(job.id);
  assert.equal(pending.status, 'published');
  assert.equal(pending.publicationState, 'cancellation_pending');
  assert.equal(pending.cancelRequested, true);
  assert.equal(pending.error, 'publication_cancellation_pending_confirmation');
  assert.equal(response.body.job.status, 'published');
  assert.equal(response.body.job.publicationState, 'cancellation_pending');
});

test('ordinary running cancellation enters durable merge arbitration before acknowledgement', async (t) => {
  const dataDir = fixture(t);
  const store = new JobStore(dataDir);
  const server = createServer({ config: config(dataDir), authorizer: async () => ({ id: 'owner' }), store, queue: passiveQueue() });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  t.after(() => server.close());
  const job = store.create({ instruction: 'write proof', authorizedScope: [PROOF_ROOT] }, 'owner', 'a'.repeat(40));
  job.status = 'running'; store.save(job);

  const response = await postCancel(server, job.id);
  assert.equal(response.status, 202);
  assert.equal(publicationDecision(dataDir, job.id), 'cancelled');
});

test('an already committed merge decision cannot be hidden by stale running job state', async (t) => {
  const dataDir = fixture(t);
  const store = new JobStore(dataDir);
  const server = createServer({ config: config(dataDir), authorizer: async () => ({ id: 'owner' }), store, queue: passiveQueue() });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  t.after(() => server.close());
  const job = store.create({ instruction: 'write proof', authorizedScope: [PROOF_ROOT] }, 'owner', 'a'.repeat(40));
  job.status = 'running'; store.save(job);
  beginPublicationMerge(dataDir, job.id);

  const response = await postCancel(server, job.id);
  assert.equal(response.status, 409);
  assert.equal(publicationDecision(dataDir, job.id), 'merge');
});

test('cancelled isolated execution requires stop reconciliation even without task ARN', () => {
  assert.equal(shouldStopCancelledExecution({ cancelRequested: true, execution: { id: 'execution', taskArn: null } }, true), true);
  assert.equal(shouldStopCancelledExecution({ cancelRequested: true, execution: { id: 'execution', taskArn: 'arn', stoppedAt: 'done' } }, true), false);
  assert.equal(shouldStopCancelledExecution({ cancelRequested: false, execution: { id: 'execution', taskArn: null } }, true), false);
  assert.equal(shouldStopCancelledExecution({ cancelRequested: true, execution: { id: 'execution', taskArn: null } }, false), false);
});

test('terminal transport removes input and reusable capability while keeping reconciliation evidence', (t) => {
  const directory = fixture(t);
  fs.writeFileSync(path.join(directory, 'auth.json'), JSON.stringify({ token: 'fixture-capability', hash: 'abcd', expires: Date.now() + 10000, maxRequests: 200 }));
  fs.writeFileSync(path.join(directory, 'input.json'), JSON.stringify({ instruction: 'private fixture instruction', archive: 'fixture archive' }));
  fs.writeFileSync(path.join(directory, 'checkpoint.json'), JSON.stringify({ events: [] }));
  fs.writeFileSync(path.join(directory, 'result.json'), JSON.stringify({ completed: true }));

  scrubTerminalTransport(directory);

  assert.equal(fs.existsSync(path.join(directory, 'input.json')), false);
  const auth = JSON.parse(fs.readFileSync(path.join(directory, 'auth.json'), 'utf8'));
  assert.equal(Object.hasOwn(auth, 'token'), false);
  assert.equal(auth.expires, 0);
  assert.equal(fs.existsSync(path.join(directory, 'checkpoint.json')), true);
  assert.equal(fs.existsSync(path.join(directory, 'result.json')), true);
});
