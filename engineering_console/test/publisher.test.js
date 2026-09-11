import test from 'node:test';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import { checksAllowMerge, validateManifest } from '../src/trusted-publisher.js';

function manifestFor(patch) {
  return {
    version: 1,
    jobId: '123e4567-e89b-42d3-a456-426614174000',
    repository: 'KirtKurt/parlay-platform',
    startingRevision: 'a'.repeat(40),
    proposedBranch: 'inqsi/job-123e4567-e89b-42d3-a456-426614174000',
    authorizedScope: ['engineering_console'],
    changedFiles: ['engineering_console/src/example.js'],
    patchSha256: crypto.createHash('sha256').update(patch).digest('hex')
  };
}

test('publisher accepts an in-scope credential-free immutable bundle', () => {
  const patch = 'diff --git a/engineering_console/src/example.js b/engineering_console/src/example.js\n';
  assert.equal(validateManifest(manifestFor(patch), patch), true);
});

test('publisher rejects a changed patch hash', () => {
  const patch = 'safe patch';
  assert.throws(() => validateManifest(manifestFor(patch), `${patch} changed`), /hash_mismatch/);
});

test('publisher rejects patch credentials before applying them', () => {
  const patch = 'Authorization: Bearer example-very-secret-token-value';
  assert.throws(() => validateManifest(manifestFor(patch), patch), /secret_detected/);
});

test('publisher rejects changed files outside the authorized scope', () => {
  const patch = 'safe patch';
  const manifest = manifestFor(patch);
  manifest.changedFiles = ['hello_world/api.py'];
  assert.throws(() => validateManifest(manifest, patch), /scope_violation/);
});

test('publisher merge gate requires at least one completed successful check', () => {
  assert.equal(checksAllowMerge([]), false);
  assert.equal(checksAllowMerge([{ status: 'in_progress', conclusion: null }]), false);
  assert.equal(checksAllowMerge([{ status: 'completed', conclusion: 'failure' }]), false);
  assert.equal(checksAllowMerge([
    { status: 'completed', conclusion: 'success' },
    { status: 'completed', conclusion: 'skipped' }
  ]), true);
});
