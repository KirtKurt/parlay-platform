import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { refreshRepository } from '../src/git.js';

function git(cwd, ...args) { return execFileSync('git', args, { cwd, encoding: 'utf8' }).trim(); }

test('refreshRepository fast-forwards the durable main checkout', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'inqsi-refresh-'));
  const source = path.join(root, 'source');
  const bare = path.join(root, 'remote.git');
  const checkout = path.join(root, 'checkout');
  fs.mkdirSync(source);
  git(source, 'init', '-b', 'main');
  git(source, 'config', 'user.email', 'test@example.invalid');
  git(source, 'config', 'user.name', 'InQsi Test');
  fs.writeFileSync(path.join(source, 'file.txt'), 'one\n');
  git(source, 'add', '.'); git(source, 'commit', '-m', 'one');
  execFileSync('git', ['clone', '--bare', source, bare]);
  execFileSync('git', ['clone', bare, checkout]);
  const before = git(checkout, 'rev-parse', 'HEAD');
  git(source, 'remote', 'add', 'origin', bare);
  fs.writeFileSync(path.join(source, 'file.txt'), 'two\n');
  git(source, 'commit', '-am', 'two'); git(source, 'push', 'origin', 'main');
  const refreshed = await refreshRepository(checkout);
  assert.notEqual(refreshed, before);
  assert.equal(fs.readFileSync(path.join(checkout, 'file.txt'), 'utf8'), 'two\n');
});

test('refreshRepository refuses a non-main root checkout', async () => {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'inqsi-refresh-branch-'));
  git(repo, 'init', '-b', 'other');
  await assert.rejects(() => refreshRepository(repo), /repository_root_not_on_main/);
});
