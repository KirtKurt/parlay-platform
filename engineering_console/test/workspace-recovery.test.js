import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { createWorkspace } from '../src/git.js';

function git(cwd, ...args) { return execFileSync('git', args, { cwd, encoding: 'utf8' }).trim(); }

function makeRepo() {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'inqsi-workspace-repo-'));
  git(repo, 'init', '-b', 'main');
  git(repo, 'config', 'user.email', 'test@example.invalid');
  git(repo, 'config', 'user.name', 'InQsi Test');
  fs.writeFileSync(path.join(repo, 'base.txt'), 'base\n');
  git(repo, 'add', '.'); git(repo, 'commit', '-m', 'base');
  return repo;
}

test('createWorkspace reuses an existing deterministic worktree without resetting changes', async () => {
  const repository = makeRepo();
  const workspaceRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'inqsi-workspace-root-'));
  const job = { id: '11111111-1111-4111-8111-111111111111', startingRevision: git(repository, 'rev-parse', 'HEAD') };
  const first = await createWorkspace({ repository, workspaceRoot }, job);
  fs.writeFileSync(path.join(first.workspace, 'partial.txt'), 'keep-me\n');
  const second = await createWorkspace({ repository, workspaceRoot }, job);
  assert.equal(second.workspace, first.workspace);
  assert.equal(fs.readFileSync(path.join(second.workspace, 'partial.txt'), 'utf8'), 'keep-me\n');
});

test('createWorkspace restores an existing job branch after its worktree is removed', async () => {
  const repository = makeRepo();
  const workspaceRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'inqsi-workspace-root-'));
  const job = { id: '22222222-2222-4222-8222-222222222222', startingRevision: git(repository, 'rev-parse', 'HEAD') };
  const first = await createWorkspace({ repository, workspaceRoot }, job);
  git(first.workspace, 'config', 'user.email', 'test@example.invalid');
  git(first.workspace, 'config', 'user.name', 'InQsi Test');
  fs.writeFileSync(path.join(first.workspace, 'committed.txt'), 'preserve\n');
  git(first.workspace, 'add', '.'); git(first.workspace, 'commit', '-m', 'partial');
  git(repository, 'worktree', 'remove', '--force', first.workspace);
  const restored = await createWorkspace({ repository, workspaceRoot }, job);
  assert.equal(fs.readFileSync(path.join(restored.workspace, 'committed.txt'), 'utf8'), 'preserve\n');
});
