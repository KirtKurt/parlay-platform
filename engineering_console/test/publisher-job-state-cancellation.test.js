import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { JobStore } from '../src/store.js';
import { cancelPublication } from '../src/publication-decision.js';
import { updateJobFromPublisher } from '../src/publisher-job-state.js';

function fixture(t) {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'inqsi-publisher-state-'));
  t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
  const store = new JobStore(dataDir);
  const job = store.create({ instruction: 'write proof', authorizedScope: ['engineering_console_publication_proof'] }, 'owner', 'a'.repeat(40));
  job.status = 'published';
  job.publicationState = 'checks_pending';
  job.pullRequest = 'https://github.com/KirtKurt/parlay-platform/pull/1';
  store.save(job);
  return { dataDir, store, job };
}

test('cancellation_pending never finalizes the job as cancelled', (t) => {
  const { dataDir, store, job } = fixture(t);
  assert.equal(cancelPublication(dataDir, job.id), true);
  updateJobFromPublisher(dataDir, {
    jobId: job.id,
    state: 'cancellation_pending',
    pullRequest: job.pullRequest,
    reason: 'github_outcome_pending'
  });
  const saved = store.get(job.id);
  assert.equal(saved.status, 'published');
  assert.equal(saved.publicationState, 'cancellation_pending');
  assert.equal(saved.cancelRequested, true);
  assert.equal(saved.error, 'github_outcome_pending');
});

test('publisher failure after accepted cancellation remains visible', (t) => {
  const { dataDir, store, job } = fixture(t);
  assert.equal(cancelPublication(dataDir, job.id), true);
  updateJobFromPublisher(dataDir, {
    jobId: job.id,
    state: 'publisher_failed',
    pullRequest: job.pullRequest,
    reason: 'github_close_unconfirmed'
  });
  const saved = store.get(job.id);
  assert.equal(saved.status, 'failed');
  assert.equal(saved.publicationState, 'publisher_failed');
  assert.equal(saved.cancelRequested, true);
  assert.equal(saved.error, 'github_close_unconfirmed');
});

test('only a confirmed cancelled receipt finalizes cancellation', (t) => {
  const { dataDir, store, job } = fixture(t);
  assert.equal(cancelPublication(dataDir, job.id), true);
  updateJobFromPublisher(dataDir, {
    jobId: job.id,
    state: 'cancelled',
    pullRequest: job.pullRequest,
    observedGitHubState: 'closed'
  });
  const saved = store.get(job.id);
  assert.equal(saved.status, 'cancelled');
  assert.equal(saved.publicationState, 'cancelled');
  assert.equal(saved.cancelRequested, true);
  assert.equal(saved.error, null);
});
