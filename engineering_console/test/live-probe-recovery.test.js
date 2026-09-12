import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';

for (const replaced of [true, false]) {
  test(`live probe tolerates controller downtime and requires replacement evidence: replaced=${replaced}`, t => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), 'console-live-probe-'));
    t.after(() => fs.rmSync(root, { recursive: true, force: true }));
    const fixture = path.join(root, 'transport.mjs');
    fs.writeFileSync(fixture, `
const source = 'a'.repeat(40), old = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', next = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
const id = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc', executionId = 'dddddddd-dddd-4ddd-8ddd-dddddddddddd';
const taskArn = 'arn:aws:ecs:region:account:task/job';
let jobs = 0, health = 0, proofPath, nonce;
globalThis.setTimeout = (fn) => { queueMicrotask(fn); return { unref() {} }; };
globalThis.fetch = async (url, options = {}) => {
  url = String(url);
  if (url.endsWith('/healthz')) return Response.json({ revision: source, execution: 'isolated-ecs', instance: ++health >= 3 && ${replaced} ? next : old });
  if (url.endsWith('/.well-known/openid-configuration')) return Response.json({ issuer: 'https://issuer.example', token_endpoint: 'https://issuer.example/token' });
  if (url === 'https://issuer.example/token') return Response.json({ access_token: 'fixture-access', expires_in: 3600 });
  if (url === 'https://console.example/v1/engineering' && options.method !== 'POST') return new Response('', { status: 401 });
  if (url === 'https://console.example/v1/engineering') {
    const input = JSON.parse(options.body);
    if (!input.instruction.includes('sleep 90')) throw new Error('active_window_missing');
    proofPath = input.instruction.match(/engineering_console_publication_proof\\/runtime-([0-9a-f]+)\\.md/)[0];
    nonce = proofPath.match(/runtime-([0-9a-f]+)/)[1];
    return Response.json({ job: { id } });
  }
  if (url.startsWith('https://console.example/v1/engineering/')) {
    jobs++;
    if (jobs === 2) return new Response('', { status: 503 });
    const job = { id, status: jobs >= 4 ? 'completed' : 'running', threadId: 'thread', execution: { id: executionId, taskArn, eventOffset: 4 } };
    if (jobs >= 3) job.controllerRecovery = { instance: next, executionId, taskArn, threadId: 'thread', eventOffset: 4 };
    if (jobs >= 4) Object.assign(job, { publicationState: 'merged', mergeCommit: 'b'.repeat(40), commit: 'c'.repeat(40), pullRequest: 'https://github.com/KirtKurt/parlay-platform/pull/1' });
    return Response.json({ job });
  }
  if (url.endsWith('/pulls/1')) return Response.json({ merged: true, merge_commit_sha: 'b'.repeat(40), head: { sha: 'c'.repeat(40) } });
  if (url.includes('/pulls/1/files')) return Response.json([{ filename: proofPath, status: 'added' }]);
  if (url.includes('/contents/')) return Response.json({ content: Buffer.from('# Runtime proof\\n' + source + '\\n' + nonce).toString('base64') });
  throw new Error('unexpected_transport_request');
};
`);
    const result = spawnSync(process.execPath, ['--import', fixture, new URL('../scripts/verify-live.mjs', import.meta.url).pathname], {
      env: { ...process.env, INQSI_ENGINEERING_CONSOLE_URL: 'https://console.example', INQSI_ENGINEERING_SOURCE_SHA: 'a'.repeat(40), INQSI_ENGINEERING_OIDC_ISSUER: 'https://issuer.example', INQSI_ENGINEERING_OIDC_AUDIENCE: 'console', ENG_CONSOLE_PROBE_CLIENT_ID: 'fixture', ENG_CONSOLE_PROBE_CLIENT_SECRET: 'fixture' },
      encoding: 'utf8', timeout: 5000
    });
    assert.equal(result.status, replaced ? 0 : 1, result.stderr);
    const records = result.stdout.trim().split('\n').filter(Boolean).map(line => JSON.parse(line));
    assert.equal(records[0].phase, 'recovery_ready');
    assert.equal(records.some(record => record.verified === true), replaced);
    if (replaced) assert.notEqual(records.at(-1).recovery.instance, records.at(-1).recovery.previousInstance);
    else assert.match(result.stderr, /active_job_restart_not_verified/);
  });
}
