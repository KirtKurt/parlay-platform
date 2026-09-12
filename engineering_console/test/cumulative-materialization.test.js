import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import crypto from 'node:crypto';
import { materializeResult } from '../src/ecs-job.js';

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'console-cumulative-'));
  t.after(() => fs.rmSync(root, { force: true, recursive: true }));
  const workspace = path.join(root, 'repo'); fs.mkdirSync(workspace);
  const git = (...args) => execFileSync('git', ['-c', 'user.name=Test', '-c', 'user.email=test@example.com', ...args], { cwd: workspace, encoding: 'utf8' }).trim();
  git('init', '-b', 'main'); fs.writeFileSync(path.join(workspace, 'README.md'), 'base\n'); git('add', '.'); git('commit', '-m', 'base');
  const base = git('rev-parse', 'HEAD'), patchPath = path.join(root, 'result.patch');
  const apply = async (patch, previous = []) => { fs.writeFileSync(patchPath, patch); await materializeResult(workspace, base, patchPath, previous); };
  return { workspace, git, base, apply };
}
function addition(file, text) {
  const hash = crypto.createHash('sha1').update(`blob ${Buffer.byteLength(text)}\0${text}`).digest('hex');
  const lines = text.trimEnd().split('\n');
  return `diff --git a/${file} b/${file}\nnew file mode 100644\nindex ${'0'.repeat(40)}..${hash}\n--- /dev/null\n+++ b/${file}\n@@ -0,0 +1,${lines.length} @@\n${lines.map(s => '+' + s).join('\n')}\n`;
}

test('continuation replaces a verified prior patch with its cumulative result and is idempotent', async t => {
  const { workspace, git, base, apply } = fixture(t);
  const file = 'engineering_console_publication_proof/first.md', other = 'engineering_console_publication_proof/second.md';
  const first = addition(file, 'first\n');
  // Deliberately reverse file order: semantic trees, not patch text order,
  // establish whether the returned cumulative result is already materialized.
  const cumulative = addition(other, 'second\n') + addition(file, 'first\ncontinued\n');
  await apply(first); await apply(cumulative, [first]); await apply(cumulative);
  assert.equal(fs.readFileSync(path.join(workspace, file), 'utf8'), 'first\ncontinued\n');
  assert.equal(fs.readFileSync(path.join(workspace, other), 'utf8'), 'second\n');
  assert.equal(git('rev-parse', 'HEAD'), base);
  await apply('', [cumulative]);
  assert.equal(git('diff', base, '--'), '');
  assert.equal(fs.existsSync(path.join(workspace, file)), false);
});

test('unknown staged work and unstaged edits are preserved for explicit reconciliation', async t => {
  const { workspace, git, apply } = fixture(t);
  const file = 'engineering_console_publication_proof/proof.md', first = addition(file, 'first\n');
  await apply(first);
  await assert.rejects(apply(addition(file, 'replacement\n')), /requires_reconciliation/);
  fs.writeFileSync(path.join(workspace, file), 'manual edit\n');
  await assert.rejects(apply(addition(file, 'replacement\n'), [first]), /requires_reconciliation/);
  assert.equal(fs.readFileSync(path.join(workspace, file), 'utf8'), 'manual edit\n');
  assert.ok(git('diff', '--', file));
});

test('untracked files cannot be overwritten during cumulative reconciliation', async t => {
  const { workspace, apply } = fixture(t);
  const file = 'engineering_console_publication_proof/proof.md';
  fs.mkdirSync(path.dirname(path.join(workspace, file)), { recursive: true }); fs.writeFileSync(path.join(workspace, file), 'untracked\n');
  await assert.rejects(apply(addition(file, 'replacement\n')), /requires_reconciliation/);
  assert.equal(fs.readFileSync(path.join(workspace, file), 'utf8'), 'untracked\n');
});
