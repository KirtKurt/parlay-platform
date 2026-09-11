import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { Codex } from '@openai/codex-sdk';
import { collectChanges } from '../src/git.js';
import { sanitizeValue } from '../src/sanitize.js';
const exec = promisify(execFile);
const root = '/job';
const workspace = path.join(root, 'workspace');
const home = path.join(root, 'home');
const broker = process.env.ENG_CONSOLE_BROKER_URL;
const token = process.env.ENG_CONSOLE_JOB_TOKEN;
const id = process.env.ENG_CONSOLE_EXECUTION_ID;
if (!/^https:\/\//.test(broker || '') || !/^ecj_[0-9a-f-]{36}_[0-9a-f]{64}$/.test(token || '') || !/^[0-9a-f-]{36}$/.test(id || '')) throw new Error('isolated_job_configuration_invalid');
if (['OPENAI_API_KEY', 'GH_TOKEN', 'GITHUB_TOKEN', 'AWS_ACCESS_KEY_ID', 'AWS_CONTAINER_CREDENTIALS_RELATIVE_URI', 'AWS_CONTAINER_CREDENTIALS_FULL_URI'].some(k => process.env[k])) throw new Error('isolated_job_reusable_credentials_forbidden');
const headers = { authorization: `Bearer ${token}`, 'content-type': 'application/json' };
async function transport(name, body) {
  const response = await fetch(`${broker}/broker/transport/${id}/${name}`, { method: body ? 'PUT' : 'GET', headers, redirect: 'error', signal: AbortSignal.timeout(60000), ...(body ? { body: JSON.stringify(body) } : {}) });
  if (!response.ok) throw new Error(`job_transport_failed_${response.status}`);
  return response.json();
}
const input = await transport('input');
fs.mkdirSync(workspace, { recursive: true }); fs.mkdirSync(home, { recursive: true, mode: 0o700 });
const archive = path.join(root, 'source.tar.gz');
fs.writeFileSync(archive, Buffer.from(input.archive, 'base64'));
await exec('tar', ['--no-same-owner', '--no-same-permissions', '-xzf', archive, '-C', workspace]);
await exec('git', ['init', '-b', 'job'], { cwd: workspace });
await exec('git', ['-c', 'core.hooksPath=/dev/null', 'add', '--force', '--all'], { cwd: workspace });
await exec('git', ['-c', 'core.hooksPath=/dev/null', '-c', 'user.name=Engineering Console', '-c', 'user.email=eng-console@localhost', 'commit', '--allow-empty', '-m', 'Isolated source snapshot'], { cwd: workspace });
const { stdout: base } = await exec('git', ['rev-parse', 'HEAD'], { cwd: workspace });
let threadId = input.threadId;
if (input.checkpoint?.diff) {
  const patch = path.join(root, 'checkpoint.patch'); fs.writeFileSync(patch, input.checkpoint.diff);
  await exec('git', ['-c', 'core.hooksPath=/dev/null', 'apply', patch], { cwd: workspace });
}
if (input.checkpoint?.sessions) {
  const sessions = path.join(root, 'sessions.tar.gz'); fs.writeFileSync(sessions, Buffer.from(input.checkpoint.sessions, 'base64'));
  // This data is untrusted and is extracted only inside this credential-free,
  // disposable task, never in the controller or publisher filesystem.
  await exec('tar', ['--no-same-owner', '--no-same-permissions', '-xzf', sessions, '-C', home]);
  threadId = input.checkpoint.threadId || threadId;
} else { threadId = null; }
const codex = new Codex({ apiKey: token, baseUrl: `${broker}/broker/v1`, env: { PATH: process.env.PATH, HOME: home, TMPDIR: os.tmpdir() } });
const options = { workingDirectory: workspace, model: input.model, sandboxMode: 'workspace-write', networkAccessEnabled: false, webSearchMode: 'disabled', approvalPolicy: 'never' };
const thread = threadId ? codex.resumeThread(threadId, options) : codex.startThread(options);
const events = []; let completed = false; let failure = false; let lastCheckpoint = 0;
async function checkpoint(final = false) {
  const result = await collectChanges(workspace, base.trim());
  let sessions = null;
  if (fs.existsSync(path.join(home, '.codex', 'sessions'))) {
    const packed = await exec('tar', ['-czf', '-', '-C', home, '.codex/sessions'], { encoding: 'buffer', maxBuffer: 24 * 1024 * 1024 });
    sessions = packed.stdout.toString('base64');
  }
  const payload = { ...result, sessions, threadId: thread.id || threadId, eventCount: events.length, events: sanitizeValue(events.slice(-500)), completed: completed && !failure };
  await transport(final ? 'result' : 'checkpoint', payload);
}
try {
  const stream = await thread.runStreamed(input.prompt);
  for await (const event of stream.events) {
    events.push(event);
    if (event.type === 'thread.started') threadId = event.thread_id;
    if (event.type === 'turn.completed') completed = true;
    if (event.type === 'turn.failed') failure = true;
    if (Date.now() - lastCheckpoint > 5000) { await checkpoint(); lastCheckpoint = Date.now(); }
  }
  if (!completed || failure) throw new Error('codex_turn_not_completed');
  await checkpoint(true);
} catch {
  try { await checkpoint(); } catch { /* preserve the last acknowledged checkpoint */ }
  process.exitCode = 1;
}
