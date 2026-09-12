import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import { JobStore } from '../src/store.js';
import { createServer } from '../src/server.js';
import { PROOF_ROOT } from '../src/publication-policy.js';

for (const state of ['cancelled', 'merge_conflict']) {
  for (const race of ['before_refresh', 'during_save']) {
    test(`cancel HTTP route preserves publisher ${state} ${race}`, async t => {
      const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'console-cancel-http-'));
      t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
      const store = new JobStore(dataDir), publisher = new JobStore(dataDir);
      const job = store.create({ instruction: 'write proof', authorizedScope: [PROOF_ROOT] }, 'owner', 'a'.repeat(40));
      job.status = 'published'; job.publicationState = 'checks_pending';
      job.pullRequest = 'https://github.com/KirtKurt/parlay-platform/pull/1';
      store.save(job);
      let expected, finished = false;
      const finish = () => {
        assert.equal(finished, false); finished = true;
        const latest = publisher.get(job.id);
        latest.publicationState = state;
        latest.status = state === 'cancelled' ? 'cancelled' : 'blocked';
        latest.cancelRequested = true;
        latest.error = state === 'cancelled' ? null : 'verified_merge_after_cancellation';
        if (state === 'merge_conflict') latest.mergeCommit = 'b'.repeat(40);
        publisher.save(latest);
        expected = publisher.get(job.id);
      };
      const save = store.save.bind(store);
      store.save = candidate => {
        if (race === 'during_save' && candidate.publicationState === 'cancellation_pending' && !finished) finish();
        return save(candidate);
      };
      const queue = { enqueue() { return true; }, cancel() { if (race === 'before_refresh') finish(); return true; } };
      const server = createServer({
        config: { dataDir, allowedScopes: [PROOF_ROOT], maxConcurrentJobs: 1, maxInstructionBytes: 100_000 },
        authorizer: async () => ({ id: 'owner' }), store, queue
      });
      await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
      t.after(() => { server.closeAllConnections(); server.close(); });
      const response = await new Promise((resolve, reject) => {
        const request = http.request({ hostname: '127.0.0.1', port: server.address().port,
          path: `/v1/engineering/${job.id}/cancel`, method: 'POST',
          headers: { 'content-type': 'application/json' }, timeout: 5000
        }, res => {
          let body = ''; res.on('data', chunk => { body += chunk; });
          res.on('end', () => resolve({ status: res.statusCode, body: JSON.parse(body) }));
        });
        request.on('error', reject);
        request.on('timeout', () => request.destroy(new Error('request_timeout')));
        request.end('{}');
      });
      assert.equal(finished, true);
      assert.equal(response.status, 202);
      assert.equal(response.body.job.publicationState, state);
      assert.equal(response.body.job.status, expected.status);
      assert.equal(response.body.job.error, expected.error);
      assert.equal(response.body.job.mergeCommit, expected.mergeCommit);
      assert.deepEqual(publisher.get(job.id), expected);
    });
  }
}
