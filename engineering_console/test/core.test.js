import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { JobStore } from '../src/store.js';
import { DurableQueue } from '../src/queue.js';
import { sanitize } from '../src/sanitize.js';
import { loadConfig } from '../src/config.js';

test('fails closed without identity configuration', () => assert.throws(() => loadConfig({}), /console disabled/));

test('accepts complete explicit security configuration', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'inqsi-config-'));
  const config = loadConfig({
    INQSI_ENGINEERING_OIDC_ISSUER: 'https://issuer.example',
    INQSI_ENGINEERING_OIDC_AUDIENCE: 'inqsi',
    INQSI_ENGINEERING_JWKS_URI: 'https://issuer.example/jwks.json',
    INQSI_ENGINEERING_ADMIN_CLAIM: 'admin',
    INQSI_ENGINEERING_REPOSITORY: path.join(root, 'repo'),
    INQSI_ENGINEERING_DATA_DIR: path.join(root, 'data'),
    INQSI_ENGINEERING_WORKSPACE_ROOT: path.join(root, 'workspaces'),
    INQSI_ENGINEERING_ORIGIN: 'https://engineering.example',
    INQSI_ENGINEERING_ALLOWED_SCOPES: 'engineering_console,frontend/app/api/engineering',
    INQSI_ENGINEERING_MAX_CONCURRENT_JOBS: '2'
  });
  assert.equal(config.allowedScopes.length, 2);
  assert.equal(config.maxConcurrentJobs, 2);
});

test('durably stores jobs and scopes history to its owner', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'inqsi-store-'));
  const store = new JobStore(dir);
  const job = store.create({ instruction: 'change docs', authorizedScope: ['docs'] }, 'admin-a', 'abc');
  assert.equal(new JobStore(dir).get(job.id).status, 'queued');
  assert.equal(store.list('admin-a').length, 1);
  assert.equal(store.list('admin-b').length, 0);
});

test('job store rejects invalid identifiers instead of resolving arbitrary paths', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'inqsi-store-'));
  const store = new JobStore(dir);
  assert.equal(store.get('../outside'), null);
  assert.equal(store.get('/tmp/outside'), null);
});

test('cancellation reaches a running worker abort signal', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'inqsi-queue-'));
  const store = new JobStore(dir);
  const job = store.create({ instruction: 'wait', authorizedScope: ['docs'] }, 'admin', 'abc');
  let aborted = false;
  const queue = new DurableQueue(store, async (j, signal) => {
    j.status = 'running'; store.save(j);
    await new Promise((resolve) => signal.addEventListener('abort', () => { aborted = true; resolve(); }));
    j.status = 'cancelled'; store.save(j);
  }, { maxConcurrent: 1 });
  queue.enqueue(job.id);
  await new Promise((resolve) => setTimeout(resolve, 10));
  queue.cancel(job.id);
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(aborted, true);
  assert.equal(store.get(job.id).status, 'cancelled');
});

test('redacts credentials from logs and artifacts', () => {
  const value = sanitize('Authorization: Bearer-secret sk-example123456789 token=abc123');
  assert.equal(value.includes('sk-example'), false);
  assert.equal(value.includes('abc123'), false);
});
