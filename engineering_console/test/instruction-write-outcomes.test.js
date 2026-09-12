import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { JobStore } from '../src/store.js';
import { DurableQueue } from '../src/queue.js';
import { createServer } from '../src/server.js';
import { updateJobFromPublisher } from '../src/publisher-job-state.js';
import { PROOF_ROOT } from '../src/publication-policy.js';

const raw = 'Write proof with placeholder password=first-value';
const input = { instruction: raw, authorizedScope: [PROOF_ROOT] };
const revision = 'a'.repeat(40);
const failure = code => Object.assign(new Error('injected_write_outcome'), { code });
function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'eng-write-outcome-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return { root, store: new JobStore(root) };
}
function loseAcknowledgment(store) {
  const save = store.save.bind(store);
  store.save = job => { save(job); throw failure('EWRITEUNKNOWN'); };
}
async function listen(t, root, store, queue) {
  const server = createServer({
    config: { dataDir: root, allowedScopes: [PROOF_ROOT], maxInstructionBytes: 100000 },
    authorizer: async () => ({ id: 'owner' }), store, queue
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  return `http://127.0.0.1:${server.address().port}/v1/engineering`;
}

test('initial committed write with lost acknowledgment returns the exact accepted prompt', t => {
  const { store } = fixture(t); loseAcknowledgment(store);
  const job = store.create(input, 'owner', revision);
  assert.equal(job.status, 'queued');
  assert.equal(store.getForExecution(job.id).instruction, raw);
  assert.doesNotMatch(fs.readFileSync(store.file(job.id), 'utf8'), /first-value/);
});

test('unreadable initial outcome reconciles before running the exact prompt', async t => {
  const { store } = fixture(t); loseAcknowledgment(store);
  const get = store.get.bind(store);
  store.get = () => { throw failure('EIO'); };
  let unknown;
  try { store.create(input, 'owner', revision); } catch (error) { unknown = error; }
  assert.equal(unknown.persistencePending, true);
  assert.equal(store.runtimeInstructions.size, 0);
  store.get = get;
  let observed;
  const queue = new DurableQueue(store, async job => { observed = job.instruction; job.status = 'completed'; await store.saveAsync(job); }, { recoveryDelayMs: 5 });
  t.after(() => queue.close());
  queue.reconcileLater(unknown.jobId);
  for (let i = 0; i < 100 && store.get(unknown.jobId)?.status !== 'completed'; i++) await new Promise(resolve => setTimeout(resolve, 10));
  assert.equal(observed, raw);
  assert.equal(store.get(unknown.jobId).status, 'completed');
  assert.equal(store.pendingInstructions.size, 0);
  assert.equal(store.runtimeInstructions.size, 0);
});

test('failed continuation leaves no rejected raw instruction cached', async t => {
  const { root, store } = fixture(t);
  const job = store.create(input, 'owner', revision); job.status = 'completed'; store.save(job);
  const queued = [];
  const base = await listen(t, root, store, { enqueue: id => queued.push(id) });
  store.save = () => { throw failure('EBUSY'); };
  const response = await fetch(`${base}/${job.id}/continue`, { method: 'POST', body: JSON.stringify({ instruction: 'password=rejected-value' }) });
  assert.equal(response.status, 500);
  assert.equal(store.runtimeInstructions.size, 0);
  assert.equal(store.get(job.id).status, 'completed');
  assert.deepEqual(queued, []);
});

test('stale continuation cannot replace a winning prompt with identical redacted bytes', t => {
  const { store } = fixture(t);
  const job = store.create(input, 'owner', revision); job.status = 'completed'; store.save(job);
  const winner = store.get(job.id), stale = store.get(job.id);
  winner.status = 'queued'; store.saveInstruction(winner, 'password=winner-value');
  stale.status = 'queued';
  assert.throws(() => store.saveInstruction(stale, 'password=loser-value'), { code: 'ESTALE' });
  assert.equal(store.getForExecution(job.id).instruction, 'password=winner-value');
});

test('continuation committed before lost acknowledgment is accepted and enqueued once', async t => {
  const { root, store } = fixture(t);
  const job = store.create(input, 'owner', revision); job.status = 'completed'; store.save(job);
  const queued = [];
  const base = await listen(t, root, store, { enqueue: id => queued.push(id) });
  loseAcknowledgment(store);
  const response = await fetch(`${base}/${job.id}/continue`, { method: 'POST', body: JSON.stringify({ instruction: 'password=accepted-value' }) });
  assert.equal(response.status, 202);
  assert.deepEqual(queued, [job.id]);
  assert.equal(store.getForExecution(job.id).instruction, 'password=accepted-value');
});

test('unreadable continuation outcome returns its identity and schedules reconciliation', async t => {
  const { root, store } = fixture(t);
  const job = store.create(input, 'owner', revision); job.status = 'completed'; store.save(job);
  const queued = [], pending = [];
  const base = await listen(t, root, store, { enqueue: id => queued.push(id), reconcileLater: id => pending.push(id) });
  const save = store.save.bind(store), get = store.get.bind(store);
  store.save = next => { save(next); store.get = () => { throw failure('EIO'); }; throw failure('EWRITEUNKNOWN'); };
  const response = await fetch(`${base}/${job.id}/continue`, { method: 'POST', body: JSON.stringify({ instruction: 'password=pending-value' }) });
  const result = await response.json();
  assert.equal(response.status, 202);
  assert.equal(result.job.id, job.id);
  assert.equal(result.job.status, 'persistence_pending');
  assert.deepEqual(queued, []);
  assert.deepEqual(pending, [job.id]);
  store.get = get;
  assert.equal(store.getForExecution(job.id).instruction, 'password=pending-value');
  assert.equal(store.pendingInstructions.size, 0);
});

for (const state of ['cancelled', 'merge_conflict', 'merged']) {
  test(`cancellation preserves a concurrent trusted ${state} receipt`, async t => {
    const { root, store } = fixture(t);
    const job = store.create(input, 'owner', revision);
    job.status = 'published'; job.publicationState = 'checks_pending'; job.pullRequest = 'https://github.com/KirtKurt/parlay-platform/pull/1'; store.save(job);
    const base = await listen(t, root, store, {
      enqueue() {},
      cancel(id) { updateJobFromPublisher(root, { jobId: id, state, observedGitHubState: state === 'cancelled' ? 'closed' : 'merged', mergeCommit: state === 'cancelled' ? undefined : 'b'.repeat(40), reason: state === 'merge_conflict' ? 'merged_after_cancellation' : undefined }); }
    });
    const response = await fetch(`${base}/${job.id}/cancel`, { method: 'POST', body: '{}' });
    assert.equal(response.status, 202);
    assert.equal((await response.json()).job.publicationState, state);
    assert.equal(store.get(job.id).publicationState, state);
    if (state === 'merge_conflict') assert.equal(store.get(job.id).error, 'merged_after_cancellation');
  });
}
