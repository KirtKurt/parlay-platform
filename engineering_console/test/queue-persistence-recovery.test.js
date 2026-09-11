import test from 'node:test';
import assert from 'node:assert/strict';
import { DurableQueue } from '../src/queue.js';

function transient(code) {
  return Object.assign(new Error(code), { code });
}

async function waitFor(predicate, timeout = 2000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  assert.fail('timed out waiting for queue recovery');
}

for (const code of ['ESTALE', 'EBUSY', 'EWRITEUNKNOWN']) {
  test(`transient ${code} requeues a stranded running job`, async () => {
    const durable = { id: `job-${code}`, status: 'queued', cancelRequested: false };
    let runs = 0;
    const store = {
      getForExecution: () => durable,
      get: () => durable,
      async saveAsync(job) { Object.assign(durable, job); },
      save(job) { Object.assign(durable, job); }
    };
    const queue = new DurableQueue(store, async () => {
      runs++;
      if (runs === 1) {
        durable.status = 'running';
        throw transient(code);
      }
      durable.status = 'completed';
    });
    queue.enqueue(durable.id);
    await waitFor(() => runs === 2 && durable.status === 'completed');
    assert.equal(queue.active.size, 0);
  });
}

test('unknown retry write outcome is reread and does not leave the job stranded', async () => {
  const durable = { id: 'job-unknown-retry', status: 'queued', cancelRequested: false };
  let runs = 0, saves = 0;
  const store = {
    getForExecution: () => durable,
    get: () => durable,
    async saveAsync(job) {
      saves++;
      Object.assign(durable, job);
      if (saves === 1) throw transient('EWRITEUNKNOWN');
    },
    save(job) { Object.assign(durable, job); }
  };
  const queue = new DurableQueue(store, async () => {
    runs++;
    if (runs === 1) {
      durable.status = 'running';
      throw transient('EBUSY');
    }
    durable.status = 'completed';
  });
  queue.enqueue(durable.id);
  await waitFor(() => runs === 2 && durable.status === 'completed');
  assert.ok(saves >= 1);
});

test('cancellation wins while a persistence retry is pending', async () => {
  const durable = { id: 'job-cancel-retry', status: 'queued', cancelRequested: false };
  let runs = 0;
  const store = {
    getForExecution: () => durable,
    get: () => durable,
    async saveAsync(job) { Object.assign(durable, job); },
    save(job) { Object.assign(durable, job); }
  };
  const queue = new DurableQueue(store, async () => {
    runs++;
    durable.status = 'running';
    throw transient('EBUSY');
  });
  queue.enqueue(durable.id);
  await waitFor(() => runs === 1 && queue.active.size === 0);
  durable.cancelRequested = true;
  await waitFor(() => durable.status === 'cancelled');
  assert.equal(runs, 1);
});
