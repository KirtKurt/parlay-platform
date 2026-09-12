import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { JobStore } from '../src/store.js';
import { markPublicationCancellationPending } from '../src/publication-cancellation.js';

function fixture(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'console-cancel-race-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const store = new JobStore(directory), publisher = new JobStore(directory);
  const job = store.create({ instruction: 'write bounded proof', authorizedScope: ['engineering_console_publication_proof'] }, 'owner', 'a'.repeat(40));
  job.status = 'published';
  job.publicationState = 'checks_pending';
  job.pullRequest = 'https://github.com/KirtKurt/parlay-platform/pull/1';
  store.save(job);
  return { store, publisher, id: job.id };
}
function publish(publisher, id, state) {
  const job = publisher.get(id);
  job.publicationState = state;
  job.status = { cancelled: 'cancelled', merged: 'completed', merge_conflict: 'blocked' }[state];
  job.cancelRequested = state !== 'merged';
  job.error = state === 'merge_conflict' ? 'verified_merge_after_cancellation' : null;
  if (state !== 'cancelled') job.mergeCommit = 'b'.repeat(40);
  publisher.save(job);
  return publisher.get(id);
}
for (const state of ['cancelled', 'merged', 'merge_conflict']) {
  test(`preserve ${state} observed before the pending-state refresh`, t => {
    const { store, publisher, id } = fixture(t);
    const expected = publish(publisher, id, state);
    store.save = () => assert.fail('terminal publication must not be rewritten');
    assert.deepEqual(markPublicationCancellationPending(store, id, 'owner'), expected);
    assert.deepEqual(publisher.get(id), expected);
  });
  test(`reconcile ${state} committed between refresh and snapshot-CAS write`, t => {
    const { store, publisher, id } = fixture(t);
    const save = store.save.bind(store);
    let expected, writes = 0;
    store.save = candidate => {
      writes++;
      expected = publish(publisher, id, state);
      return save(candidate); // Real helper/lock/CAS rejects the stale transition.
    };
    assert.deepEqual(markPublicationCancellationPending(store, id, 'owner'), expected);
    assert.equal(writes, 1);
    assert.deepEqual(publisher.get(id), expected);
  });
}
for (const visible of [true, false]) {
  test(`unconfirmed cancellation stays pending with visible PR=${visible}`, t => {
    const { store, publisher, id } = fixture(t);
    if (!visible) {
      const job = store.get(id); job.pullRequest = null;
      job.status = 'awaiting_publication'; store.save(job);
    }
    const result = markPublicationCancellationPending(store, id, 'owner');
    assert.equal(result.status, visible ? 'published' : 'awaiting_publication');
    assert.equal(result.publicationState, 'cancellation_pending');
    assert.equal(result.cancelRequested, true);
    assert.equal(result.error, 'publication_cancellation_pending_confirmation');
    assert.deepEqual(publisher.get(id), result);
  });
}
test('unknown acknowledgment after committed pending write is reconciled without rewriting', t => {
  const { store, publisher, id } = fixture(t), save = store.save.bind(store);
  let writes = 0;
  store.save = job => { writes++; save(job); throw Object.assign(new Error('unknown'), { code: 'EWRITEUNKNOWN' }); };
  const result = markPublicationCancellationPending(store, id, 'owner');
  assert.equal(writes, 1);
  assert.equal(result.publicationState, 'cancellation_pending');
  assert.deepEqual(publisher.get(id), result);
});
test('still-unknown writes stay errors with a bounded retry count', t => {
  const { store, publisher, id } = fixture(t);
  const before = publisher.get(id);
  let writes = 0;
  store.save = () => { writes++; throw Object.assign(new Error('unknown'), { code: 'EWRITEUNKNOWN' }); };
  assert.throws(() => markPublicationCancellationPending(store, id, 'owner'), { code: 'EWRITEUNKNOWN' });
  assert.equal(writes, 3);
  assert.deepEqual(publisher.get(id), before);
});
test('another owner cannot read or change cancellation state', t => {
  const { store, publisher, id } = fixture(t), before = publisher.get(id);
  assert.throws(() => markPublicationCancellationPending(store, id, 'other'), { status: 404 });
  assert.deepEqual(publisher.get(id), before);
});
test('definite lock failure does not invent confirmation or retry indefinitely', t => {
  const { store, publisher, id } = fixture(t), before = publisher.get(id);
  let writes = 0;
  store.save = () => { writes++; throw Object.assign(new Error('busy'), { code: 'EBUSY' }); };
  assert.throws(() => markPublicationCancellationPending(store, id, 'owner'), { code: 'EBUSY' });
  assert.equal(writes, 1);
  assert.deepEqual(publisher.get(id), before);
});
