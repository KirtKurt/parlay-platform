import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { JobStore } from '../src/store.js';
import { createRunner } from '../src/worker-runtime.js';

function git(cwd, ...args) { return execFileSync('git', args, { cwd, encoding: 'utf8' }).trim(); }

test('runner disables both sandbox networking and Codex web search', async () => {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'inqsi-worker-options-'));
  git(repo, 'init');
  git(repo, 'config', 'user.email', 'test@example.invalid');
  git(repo, 'config', 'user.name', 'InQsi Test');
  fs.mkdirSync(path.join(repo, 'allowed'));
  fs.writeFileSync(path.join(repo, 'allowed', 'base.txt'), 'base\n');
  git(repo, 'add', '.'); git(repo, 'commit', '-m', 'base');
  const store = new JobStore(fs.mkdtempSync(path.join(os.tmpdir(), 'inqsi-worker-jobs-')));
  const job = store.create({ instruction: 'inspect only', authorizedScope: ['allowed'] }, 'owner', git(repo, 'rev-parse', 'HEAD'));
  let observed;
  class FakeCodex {
    startThread(options) {
      observed = options;
      return { id: 'thread', async runStreamed() { return { events: (async function* () {})() }; } };
    }
    resumeThread(_id, options) { return this.startThread(options); }
  }
  await createRunner({ repository: repo, workspaceRoot: fs.mkdtempSync(path.join(os.tmpdir(), 'inqsi-worker-root-')) }, store, FakeCodex)(job, new AbortController().signal);
  assert.equal(observed.networkAccessEnabled, false);
  assert.equal(observed.webSearchMode, 'disabled');
  assert.equal(observed.approvalPolicy, 'never');
});
