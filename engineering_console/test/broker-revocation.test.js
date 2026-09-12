import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import crypto from 'node:crypto';
import { once } from 'node:events';
import { createJobBroker } from '../src/job-broker.js';
import { createTaskTransport, atomicTransportWrite } from '../src/task-transport.js';

async function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'eng-broker-revoke-'));
  const transport = createTaskTransport(root, { prompt: 'A harmless proof.' });
  const calls = [];
  const server = createJobBroker({ root, openAIKey: 'non-secret-unit-test-value', model: 'fixture-model', upstream: async (...args) => {
    calls.push(args); return new Response(JSON.stringify({ ok: true }), { headers: { 'content-type': 'application/json' } });
  }});
  server.listen(0, '127.0.0.1'); await once(server, 'listening');
  t.after(async () => { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); fs.rmSync(root, { recursive: true, force: true }); });
  return { ...transport, root, calls, origin: `http://127.0.0.1:${server.address().port}` };
}

async function stagedRequest(t, f, route, body, mutate) {
  const authFile = path.join(f.directory, 'auth.json');
  let sawAuthentication;
  const authenticated = new Promise(resolve => { sawAuthentication = resolve; });
  const originalRead = fs.readFileSync;
  fs.readFileSync = function (file, ...args) {
    const value = originalRead.call(this, file, ...args);
    if (file === authFile) sawAuthentication();
    return value;
  };
  t.after(() => { fs.readFileSync = originalRead; });
  const request = http.request(new URL(route, f.origin), {
    method: route.endsWith('/responses') ? 'POST' : 'PUT',
    headers: { authorization: `Bearer ${f.token}`, 'content-type': 'application/json' }
  });
  const response = new Promise((resolve, reject) => {
    request.once('error', reject);
    request.once('response', res => { res.resume(); res.once('end', () => resolve(res.statusCode)); });
  });
  request.write(body.slice(0, 1));
  await authenticated;
  fs.readFileSync = originalRead;
  const auth = JSON.parse(originalRead(authFile, 'utf8'));
  atomicTransportWrite(authFile, JSON.stringify(mutate(auth)));
  request.end(body.slice(1));
  return response;
}

for (const name of ['checkpoint', 'result', 'responses']) {
  for (const reason of ['revoked', 'expired', 'rotated']) {
    test(`${name}: reject authorization invalidated while request body is uploading (${reason})`, { timeout: 5000 }, async t => {
      const f = await fixture(t);
      const route = name === 'responses' ? '/broker/v1/responses' : `/broker/transport/${f.id}/${name}`;
      const payload = JSON.stringify(name === 'responses' ? { model: 'fixture-model', input: 'Harmless probe' } : { events: [], completed: true });
      const status = await stagedRequest(t, f, route, payload, auth => ({
        ...auth,
        ...(reason === 'rotated' ? { hash: crypto.randomBytes(32).toString('hex') } : { expires: reason === 'revoked' ? 0 : Date.now() - 1 })
      }));
      assert.equal(status, 401);
      assert.equal(f.calls.length, 0, 'revoked jobs must not start a new upstream operation');
      assert.equal(fs.existsSync(path.join(f.directory, `${name === 'responses' ? 'requests' : name}.json`)), false);
    });
  }
}

test('active task can still submit a result and call only the fixed model endpoint', async t => {
  const f = await fixture(t);
  const headers = { authorization: `Bearer ${f.token}`, 'content-type': 'application/json' };
  const stored = await fetch(`${f.origin}/broker/transport/${f.id}/result`, { method: 'PUT', headers, body: JSON.stringify({ completed: true }) });
  assert.equal(stored.status, 201); await stored.arrayBuffer();
  const response = await fetch(`${f.origin}/broker/v1/responses`, { method: 'POST', headers, body: JSON.stringify({ model: 'fixture-model', input: 'Harmless probe', store: true }) });
  assert.equal(response.status, 200); await response.arrayBuffer();
  assert.equal(f.calls.length, 1);
  assert.equal(f.calls[0][0], 'https://api.openai.com/v1/responses');
  assert.equal(JSON.parse(f.calls[0][1].body).store, false);
});

test('another task cannot write this task result', async t => {
  const f = await fixture(t), other = createTaskTransport(f.root, { prompt: 'Other proof.' });
  const response = await fetch(`${f.origin}/broker/transport/${f.id}/result`, { method: 'PUT', headers: { authorization: `Bearer ${other.token}` }, body: '{}' });
  assert.equal(response.status, 403); await response.arrayBuffer();
  assert.equal(fs.existsSync(path.join(f.directory, 'result.json')), false);
});
