import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { createJobBroker } from '../src/job-broker.js';
import { createTaskTransport, atomicTransportWrite } from '../src/task-transport.js';
import { runWithRuntimeLock } from '../src/publisher-lock.js';

function rootFor(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'console-quota-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return root;
}
async function start(t, root) {
  let dispatched = 0;
  const server = createJobBroker({ root, openAIKey: 'offline-test-only', model: 'test-model', upstream: async () => {
    dispatched++;
    // Requests overlap across the upstream await, not the synchronous counter
    // section. No real provider request is performed.
    await delay(20);
    return Response.json({ ok: true });
  } });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => { server.closeAllConnections(); server.close(); });
  return { server, url: `http://127.0.0.1:${server.address().port}`, count: () => dispatched };
}
function setCount(task, count) {
  atomicTransportWrite(path.join(task.directory, 'requests.json'), JSON.stringify({ count }));
}
async function burst(url, task, count) {
  return Promise.all(Array.from({ length: count }, async () => {
    const response = await fetch(`${url}/broker/v1/responses`, {
      method: 'POST', headers: { authorization: `Bearer ${task.token}`, 'content-type': 'application/json' },
      body: JSON.stringify({ model: 'test-model', input: 'harmless offline quota test' }),
      signal: AbortSignal.timeout(10000)
    });
    await response.arrayBuffer();
    return response.status;
  }));
}
for (const [initial, requests] of [[0, 240], [199, 64], [200, 32]]) {
  test(`singleton broker admits exactly ${200 - initial} of ${requests} concurrent requests at count ${initial}`, { timeout: 15000 }, async t => {
    const root = rootFor(t), task = createTaskTransport(root, {});
    setCount(task, initial);
    const broker = await start(t, root);
    const statuses = await burst(broker.url, task, requests);
    assert.equal(statuses.filter(status => status === 200).length, 200 - initial);
    assert.equal(statuses.filter(status => status === 429).length, requests - (200 - initial));
    assert.equal(broker.count(), 200 - initial);
    assert.equal(JSON.parse(fs.readFileSync(path.join(task.directory, 'requests.json'))).count, 200);
  });
}
test('concurrent requests do not share one job quota with another job', { timeout: 15000 }, async t => {
  const root = rootFor(t), a = createTaskTransport(root, {}), b = createTaskTransport(root, {});
  setCount(a, 199); setCount(b, 199);
  const broker = await start(t, root);
  const results = await Promise.all([burst(broker.url, a, 16), burst(broker.url, b, 16)]);
  for (const statuses of results) {
    assert.equal(statuses.filter(status => status === 200).length, 1);
    assert.equal(statuses.filter(status => status === 429).length, 15);
  }
  assert.equal(broker.count(), 2);
});
test('a broker restart preserves the exhausted durable quota', { timeout: 15000 }, async t => {
  const root = rootFor(t), task = createTaskTransport(root, {});
  setCount(task, 199);
  const first = await start(t, root);
  assert.deepEqual(await burst(first.url, task, 1), [200]);
  first.server.closeAllConnections(); await new Promise(resolve => first.server.close(resolve));
  const replacement = await start(t, root);
  assert.deepEqual(await burst(replacement.url, task, 4), [429, 429, 429, 429]);
  assert.equal(replacement.count(), 0);
});
test('shared broker.lock excludes a second broker process and is released on exit', { timeout: 10000 }, async t => {
  const root = rootFor(t), directory = path.join(root, 'locks'), script = path.join(root, 'holder.mjs');
  const first = path.join(root, 'first'), second = path.join(root, 'second');
  fs.writeFileSync(script, "import fs from 'node:fs'; fs.writeFileSync(process.argv[2], String(process.pid)); if(process.argv[3]==='hold') setTimeout(()=>{},8000);\n");
  const options = { lockName: 'broker.lock', stdio: 'ignore', env: { PATH: process.env.PATH } };
  let pid;
  const held = runWithRuntimeLock(directory, script, { ...options, args: [first, 'hold'] }).catch(error => error);
  t.after(() => { if (pid) { try { process.kill(pid, 'SIGTERM'); } catch {} } });
  const deadline = Date.now() + 5000;
  while (!fs.existsSync(first)) { assert.ok(Date.now() < deadline, 'first holder did not start'); await delay(10); }
  pid = Number(fs.readFileSync(first, 'utf8'));
  assert.equal(await runWithRuntimeLock(directory, script, { ...options, args: [second] }), 75);
  assert.equal(fs.existsSync(second), false);
  process.kill(pid, 'SIGTERM'); pid = undefined;
  assert.match((await held).message, /publisher_process_interrupted/);
  assert.equal(await runWithRuntimeLock(directory, script, { ...options, args: [second] }), 0);
  assert.equal(fs.existsSync(second), true);
});
