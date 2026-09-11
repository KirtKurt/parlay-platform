import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import { JobStore } from '../src/store.js';
import { createServer } from '../src/server.js';
import { PROOF_ROOT } from '../src/publication-policy.js';

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
  const address = server.address();

  const response = await new Promise((resolve, reject) => {
    const request = http.request({
      hostname: '127.0.0.1', port: address.port,
      path: `/v1/engineering/${job.id}/cancel`, method: 'POST',
      headers: { 'content-type': 'application/json' }
    }, (res) => {
      let body = '';
      res.on('data', (chunk) => { body += chunk; });
      res.on('end', () => resolve({ status: res.statusCode, body: JSON.parse(body) }));
    });
    request.on('error', reject);
    request.end('{}');
  });

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
