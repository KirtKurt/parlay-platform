import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { collectChanges } from '../src/git.js';
import { JobStore } from '../src/store.js';
import { createRunner } from '../src/worker-runtime.js';

function runGit(cwd, ...args) {
  return execFileSync('git', args, { cwd, encoding: 'utf8' }).trim();
}

function makeRepo() {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'inqsi-git-'));
  runGit(repo, 'init');
  runGit(repo, 'config', 'user.email', 'test@example.invalid');
  runGit(repo, 'config', 'user.name', 'InQsi Test');
  fs.mkdirSync(path.join(repo, 'allowed'), { recursive: true });
  fs.writeFileSync(path.join(repo, 'allowed', 'old.txt'), 'base\n');
  runGit(repo, 'add', '.');
  runGit(repo, 'commit', '-m', 'base');
  return repo;
}

test('collectChanges sees committed rename and untracked contents from base revision', async () => {
  const repo = makeRepo();
  const base = runGit(repo, 'rev-parse', 'HEAD');
  runGit(repo, 'mv', 'allowed/old.txt', 'allowed/new.txt');
  runGit(repo, 'commit', '-am', 'rename');
  fs.writeFileSync(path.join(repo, 'allowed', 'untracked.txt'), 'untracked-evidence\n');
  const result = await collectChanges(repo, base);
  assert.equal(result.changedFiles.includes('allowed/old.txt'), true);
  assert.equal(result.changedFiles.includes('allowed/new.txt'), true);
  assert.equal(result.changedFiles.includes('allowed/untracked.txt'), true);
  assert.match(result.diff, /untracked-evidence/);
});

test('runner fails closed on an empty Codex event stream', async () => {
  const repo = makeRepo();
  const store = new JobStore(fs.mkdtempSync(path.join(os.tmpdir(), 'inqsi-jobs-')));
  const workspaceRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'inqsi-workspaces-'));
  const job = store.create({ instruction: 'no operation', authorizedScope: ['allowed'] }, 'owner', runGit(repo, 'rev-parse', 'HEAD'));

  class EmptyCodex {
    startThread() {
      return {
        id: 'thread-empty',
        async runStreamed() { return { events: (async function* () {})() }; }
      };
    }
    resumeThread() { return this.startThread(); }
  }

  await createRunner({ repository: repo, workspaceRoot }, store, EmptyCodex)(job, new AbortController().signal);
  const result = store.get(job.id);
  assert.equal(result.status, 'failed');
  assert.match(result.error, /event_stream_empty/);
});

test('runner fails when Codex writes outside the authorized scope', async () => {
  const repo = makeRepo();
  const store = new JobStore(fs.mkdtempSync(path.join(os.tmpdir(), 'inqsi-jobs-')));
  const workspaceRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'inqsi-workspaces-'));
  const job = store.create({ instruction: 'change allowed files only', authorizedScope: ['allowed'] }, 'owner', runGit(repo, 'rev-parse', 'HEAD'));

  class ScopeCodex {
    startThread(options) {
      return {
        id: 'thread-scope',
        async runStreamed() {
          fs.writeFileSync(path.join(options.workingDirectory, 'outside.txt'), 'outside\n');
          return { events: (async function* () {
            yield { type: 'thread.started', thread_id: 'thread-scope' };
            yield { type: 'turn.completed', usage: {} };
          })() };
        }
      };
    }
    resumeThread(_id, options) { return this.startThread(options); }
  }

  await createRunner({ repository: repo, workspaceRoot }, store, ScopeCodex)(job, new AbortController().signal);
  const result = store.get(job.id);
  assert.equal(result.status, 'failed');
  assert.match(result.error, /scope_violation/);
});
