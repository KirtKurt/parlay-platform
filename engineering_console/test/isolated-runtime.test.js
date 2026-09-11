import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { createTaskTransport } from '../src/task-transport.js';
import { createJobBroker } from '../src/job-broker.js';
import { validateIsolatedTaskDefinition } from '../src/ecs-job.js';

const image = `111111111111.dkr.ecr.us-east-1.amazonaws.com/eng-console-runtime@sha256:${'a'.repeat(64)}`;
const task = () => ({ family: 'eng-console-job', requiresCompatibilities: ['FARGATE'], networkMode: 'awsvpc', volumes: [{ name: 'scratch' }], containerDefinitions: [{ name: 'job', image, user: '10001:10001', readonlyRootFilesystem: true, mountPoints: [{ sourceVolume: 'scratch', containerPath: '/job' }], command: ['node', '/app/scripts/isolated-job.mjs'], entryPoint: ['/usr/bin/tini', '--'] }] });
test('coding task has an independent filesystem and no AWS task role or injected secrets', () => {
  assert.doesNotThrow(() => validateIsolatedTaskDefinition(task(), image));
  for (const mutate of [x => x.taskRoleArn = 'arn:aws:iam::111111111111:role/controller', x => x.volumes[0].efsVolumeConfiguration = {}, x => x.containerDefinitions[0].secrets = [{ name: 'OPENAI_API_KEY', valueFrom: 'secret' }], x => x.pidMode = 'task', x => x.containerDefinitions[0].image = image.replace(/a{64}/, 'b'.repeat(64)), x => x.containerDefinitions[0].readonlyRootFilesystem = false]) {
    const value = task(); mutate(value); assert.throws(() => validateIsolatedTaskDefinition(value, image));
  }
});

async function fixture(t, upstream = async () => new Response('data: done\n\n', { headers: { 'content-type': 'text/event-stream' } })) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'eng-console-broker-test-'));
  const a = createTaskTransport(root, { instruction: 'owner A' });
  const b = createTaskTransport(root, { instruction: 'owner B' });
  const server = createJobBroker({ root, openAIKey: 'upstream-private-key', model: 'test-model', upstream });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(async () => { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); fs.rmSync(root, { recursive: true, force: true }); });
  return { a, b, root, url: `http://127.0.0.1:${server.address().port}` };
}
test('job capability can read only its own input and cannot write input', async t => {
  const { a, b, url } = await fixture(t);
  const headers = { authorization: `Bearer ${a.token}` };
  const own = await fetch(`${url}/broker/transport/${a.id}/input`, { headers });
  assert.equal((await own.json()).instruction, 'owner A');
  assert.equal((await fetch(`${url}/broker/transport/${b.id}/input`, { headers })).status, 403);
  assert.equal((await fetch(`${url}/broker/transport/${a.id}/input`, { method: 'PUT', headers, body: '{}' })).status, 405);
  assert.equal((await fetch(`${url}/broker/transport/${a.id}/input`)).status, 401);
});
test('broker restricts upstream route/model and never returns an upstream credential error', async t => {
  const seen = [];
  const { a, url } = await fixture(t, async (...args) => { seen.push(args); return new Response('incorrect key: upstream-private-key', { status: 401 }); });
  const headers = { authorization: `Bearer ${a.token}`, 'content-type': 'application/json' };
  const badRoute = await fetch(`${url}/broker/v1/files`, { method: 'POST', headers, body: '{}' });
  assert.equal(badRoute.status, 404);
  const badModel = await fetch(`${url}/broker/v1/responses`, { method: 'POST', headers, body: JSON.stringify({ model: 'other' }) });
  assert.equal(badModel.status, 403); assert.equal(seen.length, 0);
  const response = await fetch(`${url}/broker/v1/responses`, { method: 'POST', headers, body: JSON.stringify({ model: 'test-model', input: 'hello' }) });
  assert.equal(response.status, 401); assert.equal((await response.text()).includes('upstream-private-key'), false);
  assert.equal(seen[0][0], 'https://api.openai.com/v1/responses');
  assert.equal(seen[0][1].headers.authorization, 'Bearer upstream-private-key');
  assert.equal(JSON.parse(seen[0][1].body).store, false);
});
test('expired capability cannot use the model or read prior job state', async t => {
  const { a, url } = await fixture(t);
  const file = path.join(a.directory, 'auth.json'); const auth = JSON.parse(fs.readFileSync(file)); auth.expires = 1; fs.writeFileSync(file, JSON.stringify(auth));
  assert.equal((await fetch(`${url}/broker/transport/${a.id}/input`, { headers: { authorization: `Bearer ${a.token}` } })).status, 401);
});
