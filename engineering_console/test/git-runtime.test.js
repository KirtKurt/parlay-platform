import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { collectChanges } from '../src/git.js';

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
