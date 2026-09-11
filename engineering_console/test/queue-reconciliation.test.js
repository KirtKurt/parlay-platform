import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { JobStore } from '../src/store.js';
import { DurableQueue } from '../src/queue.js';

async function until(predicate) {
  const deadline = Date.now() + 5000;
  while (!predicate()) {
    if (Date.now() >= deadline) assert.fail('queue failed to reconcile');
    await new Promise(resolve => setTimeout(resolve, 10));
  }
}
for (const code of ['ESTALE', 'EBUSY', 'EWRITEUNKNOWN']) {
  test(`reconcile ${code} from the latest durable record without waiting for server restart`, async t => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), 'console-reconcile-'));
    const store = new JobStore(root);
    const job = store.create({ instruction: 'Write proof', authorizedScope: ['engineering_console_publication_proof'] }, 'owner', 'a'.repeat(40));
    let attempts = 0;
    const queue = new DurableQueue(store, async current => {
      attempts++;
      if (attempts === 1) {
        current.status = 'running'; current.threadId = 'durable-thread'; await store.saveAsync(current);
        throw Object.assign(new Error('temporary write outcome'), { code });
      }
      assert.equal(current.threadId, 'durable-thread');
      current.status = 'completed'; await store.saveAsync(current);
    }, { recoveryDelayMs: 1 });
    t.after(() => { queue.close(); fs.rmSync(root, { recursive: true, force: true }); });
    queue.enqueue(job.id);
    await until(() => store.get(job.id).status === 'completed' && queue.active.size === 0);
    assert.equal(attempts, 2); assert.equal(store.hasRuntimeInstruction(job.id), false);
  });
}
test('unknown acknowledgment after committed completion does not rerun completed work', async t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'console-reconcile-terminal-'));
  const store = new JobStore(root);
  const job = store.create({ instruction: 'Write proof', authorizedScope: ['engineering_console_publication_proof'] }, 'owner', 'a'.repeat(40));
  let attempts = 0;
  const queue = new DurableQueue(store, async current => {
    attempts++; current.status = 'completed'; await store.saveAsync(current);
    throw Object.assign(new Error('acknowledgment lost'), { code: 'EWRITEUNKNOWN' });
  }, { recoveryDelayMs: 1 });
  t.after(() => { queue.close(); fs.rmSync(root, { recursive: true, force: true }); });
  queue.enqueue(job.id);
  await until(() => attempts === 1 && queue.active.size === 0 && queue.recoveries.size === 0);
  assert.equal(attempts, 1); assert.equal(store.get(job.id).status, 'completed');
});
