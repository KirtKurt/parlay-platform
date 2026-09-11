import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { jwtVerify, SignJWT, createLocalJWKSet, exportJWK } from 'jose';
import { createBrowserAuth, ACCESS_COOKIE } from '../src/browser-auth.js';
import { createAuthorizer } from '../src/auth.js';
import { createInstallationTokenProvider } from '../src/github-app-token.js';
import { createTaskTransport, authenticateTask, atomicTransportWrite } from '../src/task-transport.js';
import { EcsCodex, stopExecution, validateReturnedPatch } from '../src/ecs-job.js';

const config = { issuer: 'https://issuer.example', jwksUri: 'https://issuer.example/jwks', audience: 'console', adminClaim: 'admin', allowedOrigin: 'https://console.example', oidcClientId: 'browser-client', oidcClientSecret: 'test-client-secret', sessionKey: 's'.repeat(40) };
function response() { return { status: null, headers: {}, body: '', writeHead(status, headers) { this.status = status; this.headers = headers; }, end(body) { this.body = body; } }; }
test('browser cookie authentication rejects cross-origin, missing-origin and duplicate-cookie writes', async () => {
  const authorize = createAuthorizer(config, async token => { assert.equal(token, 'signed-access'); return { payload: { sub: 'admin-user', admin: true } }; });
  const headers = { cookie: `${ACCESS_COOKIE}=signed-access` };
  assert.equal((await authorize({ method: 'GET', headers })).id, 'admin-user');
  for (const origin of [undefined, 'https://attacker.example']) await assert.rejects(authorize({ method: 'POST', headers: { ...headers, origin } }), /origin_not_allowed/);
  assert.equal((await authorize({ method: 'POST', headers: { ...headers, origin: config.allowedOrigin } })).id, 'admin-user');
  await assert.rejects(authorize({ method: 'GET', headers: { cookie: `${headers.cookie}; ${headers.cookie}` } }), /authentication_required/);
});

test('browser login verifies PKCE, state, signed ID token, nonce and administrator subject', async () => {
  const { privateKey, publicKey } = crypto.generateKeyPairSync('rsa', { modulusLength: 2048 });
  const jwks = createLocalJWKSet({ keys: [await exportJWK(publicKey)] });
  let flow, exchanges = 0, invalidNonce = false;
  const request = async (url, options) => {
    if (url.endsWith('openid-configuration')) return Response.json({ issuer: config.issuer, authorization_endpoint: `${config.issuer}/authorize`, token_endpoint: `${config.issuer}/token` });
    exchanges++;
    assert.equal(options.body.get('code_verifier'), flow.verifier);
    const id = await new SignJWT({ nonce: invalidNonce ? 'wrong' : flow.nonce }).setProtectedHeader({ alg: 'RS256' }).setSubject('admin-user').setIssuer(config.issuer).setAudience(config.oidcClientId).setExpirationTime('5m').sign(privateKey);
    return Response.json({ id_token: id, access_token: 'signed-access', expires_in: 300 });
  };
  const auth = createBrowserAuth(config, async req => { assert.equal(req.headers.authorization, 'Bearer signed-access'); return { id: 'admin-user' }; }, request, (token, _, options) => jwtVerify(token, jwks, options));
  const login = response(); await auth({ method: 'GET', url: '/auth/login', headers: {} }, login);
  assert.equal(login.status, 302);
  const cookie = login.headers['set-cookie'][0].split(';')[0];
  ({ payload: flow } = await jwtVerify(cookie.slice(cookie.indexOf('=') + 1), new TextEncoder().encode(config.sessionKey)));
  const target = new URL(login.headers.location);
  assert.equal(target.searchParams.get('code_challenge'), crypto.createHash('sha256').update(flow.verifier).digest('base64url'));
  assert.match(login.headers['set-cookie'][0], /HttpOnly; Secure; SameSite=Lax/);
  const invalid = response(); await auth({ method: 'GET', url: '/auth/callback?state=wrong&code=code', headers: { cookie } }, invalid);
  assert.equal(invalid.status, 401); assert.equal(exchanges, 0);
  const callback = { method: 'GET', url: `/auth/callback?state=${flow.state}&code=code`, headers: { cookie } };
  invalidNonce = true; const wrongNonce = response(); await auth(callback, wrongNonce); assert.equal(wrongNonce.status, 401);
  invalidNonce = false; const valid = response(); await auth(callback, valid); assert.equal(valid.status, 302);
  assert.match(valid.headers['set-cookie'][1], /__Host-inqsi_engineering_access=signed-access; HttpOnly; Secure; SameSite=Strict; Path=\//);
});

test('publisher renews short-lived GitHub App credentials and redacts failed refresh details', async () => {
  const { privateKey, publicKey } = crypto.generateKeyPairSync('rsa', { modulusLength: 2048 });
  let now = Date.now(), loads = 0, requests = 0, fail = false;
  const provider = createInstallationTokenProvider({ secretArn: 'arn:aws:secretsmanager:us-east-1:111111111111:secret:eng-console-app', now: () => now,
    load: async () => { loads++; return { SecretString: JSON.stringify({ appId: 123, installationId: 456, privateKey: privateKey.export({ type: 'pkcs8', format: 'pem' }) }) }; },
    request: async (url, options) => {
      requests++; if (fail) throw new Error('do not expose secret-key');
      assert.equal(url, 'https://api.github.com/app/installations/456/access_tokens');
      const { payload } = await jwtVerify(options.headers.authorization.slice(7), publicKey, { currentDate: new Date(now), issuer: '123' });
      assert.ok(payload.exp - payload.iat <= 600);
      assert.deepEqual(JSON.parse(options.body).repositories, ['parlay-platform']);
      return Response.json({ token: `ghs_test${requests}`, expires_at: new Date(now + 3600000).toISOString() });
    }
  });
  assert.equal(await provider(), 'ghs_test1'); assert.equal(await provider(), 'ghs_test1'); assert.equal(loads, 1);
  now += 56 * 60000; assert.equal(await provider(), 'ghs_test2'); assert.equal(loads, 2);
  now += 56 * 60000; fail = true; await assert.rejects(provider(), /^Error: publisher_installation_token_unavailable$/);
});

function temporary(t) { const root = fs.mkdtempSync(path.join(os.tmpdir(), 'console-boundary-')); t.after(() => fs.rmSync(root, { force: true, recursive: true })); return root; }
test('cancellation expires the capability and waits for confirmed STOPPED', async t => {
  const root = temporary(t); const transport = createTaskTransport(root, {});
  const job = { execution: { id: transport.id, taskArn: 'task/1' } }; let describes = 0, saves = 0;
  const aws = async (_, operation) => operation === 'stop-task' ? {} : { tasks: [{ taskArn: 'task/1', lastStatus: ++describes === 2 ? 'STOPPED' : 'STOPPING' }] };
  await stopExecution({ transportDir: root, cluster: 'test' }, job, { save: () => saves++ }, aws, { pollMs: 1, attempts: 3 });
  assert.equal(describes, 2); assert.equal(saves, 1); assert.ok(job.execution.stoppedAt);
  assert.throws(() => authenticateTask(root, `Bearer ${transport.token}`));
});
test('unconfirmed task stop never records terminal cancellation', async t => {
  const root = temporary(t); const transport = createTaskTransport(root, {});
  const job = { execution: { id: transport.id, taskArn: 'task/1' } };
  await assert.rejects(stopExecution({ transportDir: root, cluster: 'test' }, job, { save() { assert.fail('cannot save stopped'); } }, async () => ({}), { pollMs: 1, attempts: 2 }), /stop_unconfirmed/);
  assert.equal(job.execution.stoppedAt, undefined);
});

test('remote task recovery uses the durable task identity and applies an approved result only once', async t => {
  const root = temporary(t); const workspace = path.join(root, 'repo'); fs.mkdirSync(workspace);
  const git = (...args) => execFileSync('git', ['-c', 'user.name=Test', '-c', 'user.email=test@example.com', ...args], { cwd: workspace, encoding: 'utf8' }).trim();
  git('init', '-b', 'main'); fs.writeFileSync(path.join(workspace, 'README.md'), 'base\n'); git('add', '.'); git('commit', '-m', 'base');
  const startingRevision = git('rev-parse', 'HEAD');
  const transportDir = path.join(root, 'transport'); fs.mkdirSync(transportDir); const transport = createTaskTransport(transportDir, {});
  const file = 'engineering_console_publication_proof/test.md';
  const hash = execFileSync('git', ['hash-object', '--stdin'], { cwd: workspace, input: 'proof\n', encoding: 'utf8' }).trim();
  const patch = `diff --git a/${file} b/${file}\nnew file mode 100644\nindex ${'0'.repeat(40)}..${hash}\n--- /dev/null\n+++ b/${file}\n@@ -0,0 +1 @@\n+proof\n`;
  const output = { completed: true, events: [{ type: 'turn.completed' }], changedFiles: [file], diff: patch, threadId: crypto.randomUUID() };
  atomicTransportWrite(path.join(transport.directory, 'result.json'), JSON.stringify(output), { immutable: true });
  const image = `111111111111.dkr.ecr.us-east-1.amazonaws.com/eng-console-runtime@sha256:${'a'.repeat(64)}`;
  const definition = { family: 'eng-console-job', networkMode: 'awsvpc', requiresCompatibilities: ['FARGATE'], containerDefinitions: [{ name: 'job', image, readonlyRootFilesystem: true, user: '10001:10001', command: ['node', '/app/scripts/isolated-job.mjs'], entryPoint: ['/usr/bin/tini', '--'] }] };
  const job = { id: crypto.randomUUID(), startingRevision, authorizedScope: ['engineering_console_publication_proof'], execution: { id: transport.id, taskArn: 'task/durable', requestedAt: new Date().toISOString() } };
  const cfg = { cluster: 'eng-console-runtime', jobTaskDefinition: 'definition', jobImage: image, jobSecurityGroup: 'sg-test', brokerUrl: 'https://console.example', transportDir, model: 'test-model', jobSubnets: ['a', 'b'], allowedScopes: job.authorizedScope, requiredChecks: ['engineering-console-publication-proof'] };
  const callAws = async (_, operation, input) => {
    if (operation === 'describe-task-definition') return definition;
    assert.equal(operation, 'describe-tasks'); assert.deepEqual(input.tasks, ['task/durable']);
    return { tasks: [{ taskArn: 'task/durable', lastStatus: 'STOPPED', containers: [{ exitCode: 0 }] }] };
  };
  const run = new EcsCodex({ config: cfg, job, workspace, store: { save() {} }, callAws });
  for (let attempt = 0; attempt < 2; attempt++) {
    const events = []; for await (const event of run.run('proof', null, {}, new AbortController().signal)) events.push(event);
    assert.equal(events.at(-1).type, 'turn.completed');
    assert.equal(fs.readFileSync(path.join(workspace, file), 'utf8'), 'proof\n');
    assert.equal(git('diff', '--binary', '--full-index', '--no-ext-diff', '--no-textconv', startingRevision, '--'), patch.trim());
  }
  assert.throws(() => validateReturnedPatch(patch.replaceAll(file, 'engineering_console/evil.md'), [file]), /path_forbidden/);
  assert.throws(() => validateReturnedPatch(patch.replace('100644', '120000'), [file]), /mode_forbidden/);
});
