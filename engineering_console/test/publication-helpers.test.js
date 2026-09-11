import test from 'node:test';
import assert from 'node:assert/strict';
import { evaluateRequiredChecks, normalizeRepoPath } from '../src/publication.js';

test('missing required check stays pending', () => {
  assert.equal(evaluateRequiredChecks([], ['build']).state, 'pending');
});

test('completed required check must pass', () => {
  assert.equal(evaluateRequiredChecks([{ id: 1, name: 'build', status: 'completed', conclusion: 'success' }], ['build']).state, 'passed');
  assert.equal(evaluateRequiredChecks([{ id: 2, name: 'build', status: 'completed', conclusion: 'failure' }], ['build']).state, 'failed');
});

test('ambiguous repository paths are rejected', () => {
  assert.equal(normalizeRepoPath('engineering_console/src'), 'engineering_console/src');
  assert.equal(normalizeRepoPath('engineering_console/./src'), null);
  assert.equal(normalizeRepoPath('../engineering_console'), null);
  assert.equal(normalizeRepoPath('engineering_console\\src'), null);
});
