import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { JobStore } from '../src/store.js';

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'eng-instruction-lifecycle-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const store = new JobStore(root);
  return { root, store };
}
const input = () => ({ instruction: 'Create a harmless proof document.', authorizedScope: ['engineering_console_publication_proof'] });
const revision = 'a'.repeat(40);
for (const status of ['completed', 'failed', 'cancelled', 'blocked', 'awaiting_publication', 'published', 'awaiting_approval']) {
  test(`release cached instruction after a committed ${status} transition`, async t => {
    const { store } = fixture(t); const job = store.create(input(), 'owner', revision);
    assert.equal(store.hasRuntimeInstruction(job.id), true);
    job.status = status;
    await store.saveAsync(job);
    assert.equal(store.hasRuntimeInstruction(job.id), false);
    assert.equal(store.get(job.id).status, status);
  });
}
for (const status of ['queued', 'running']) {
  test(`retain exact authorized instruction while ${status}`, async t => {
    const { store } = fixture(t); const job = store.create(input(), 'owner', revision);
    job.status = status; await store.saveAsync(job);
    assert.equal(store.hasRuntimeInstruction(job.id), true);
    assert.equal(store.getForExecution(job.id).instruction, input().instruction);
  });
}
test('observation of terminal state committed by another store releases the local cache', t => {
  const { store, root } = fixture(t); const job = store.create(input(), 'owner', revision);
  const other = new JobStore(root), copy = other.get(job.id); copy.status = 'cancelled'; other.save(copy);
  assert.equal(store.get(job.id).status, 'cancelled');
  assert.equal(store.hasRuntimeInstruction(job.id), false);
});
test('fresh continuation explicitly repopulates only its own instruction', t => {
  const { store } = fixture(t); const job = store.create(input(), 'owner', revision);
  job.status = 'completed'; store.save(job); assert.equal(store.hasRuntimeInstruction(job.id), false);
  const continuation = store.get(job.id); continuation.instruction = 'A new harmless proof.';
  store.rememberInstruction(job.id, continuation.instruction); continuation.status = 'queued'; store.save(continuation);
  assert.equal(store.getForExecution(job.id).instruction, continuation.instruction);
  assert.equal(store.hasRuntimeInstruction(job.id), true);
});
test('failed initial persistence does not retain an unreachable instruction', t => {
  const { store } = fixture(t);
  store.save = () => { throw Object.assign(new Error('fixture_busy'), { code: 'EBUSY' }); };
  assert.throws(() => store.create(input(), 'owner', revision), { code: 'EBUSY' });
  assert.equal(store.runtimeInstructions.size, 0);
});
for (const instruction of [123, true, {}, [], null, undefined, '', '  ']) {
  test(`invalid create instruction ${JSON.stringify(instruction)} is a client error before persistence`, t => {
    const { store, root } = fixture(t);
    assert.throws(() => store.create({ ...input(), instruction }, 'owner', revision), { status: 400, code: 'EINVAL' });
    assert.equal(store.runtimeInstructions.size, 0);
    assert.equal(fs.readdirSync(root).length, 0);
  });
}
test('sanitized persisted instruction is not replaced with raw text at completion', t => {
  const { store } = fixture(t); const raw = 'Update the documentation placeholder password=example-value.';
  const job = store.create({ ...input(), instruction: raw }, 'owner', revision);
  assert.equal(store.getForExecution(job.id).instruction, raw);
  assert.notEqual(store.get(job.id).instruction, raw);
  job.status = 'completed'; store.save(job);
  assert.equal(store.hasRuntimeInstruction(job.id), false);
  assert.notEqual(job.instruction, raw);
  assert.doesNotMatch(fs.readFileSync(store.file(job.id), 'utf8'), /example-value/);
});
