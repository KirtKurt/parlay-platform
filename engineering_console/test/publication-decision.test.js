import test from 'node:test';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { beginPublicationMerge, cancelPublication, publicationDecision } from '../src/publication-decision.js';

function fixture(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'inqsi-publication-decision-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  return { dir, id: crypto.randomUUID() };
}

test('accepted cancellation permanently wins before merge commitment', (t) => {
  const { dir, id } = fixture(t);
  assert.equal(cancelPublication(dir, id), true);
  assert.equal(publicationDecision(dir, id), 'cancelled');
  assert.throws(() => beginPublicationMerge(dir, id), /publication_cancelled/);
});

test('merge commitment permanently wins before later cancellation', (t) => {
  const { dir, id } = fixture(t);
  assert.equal(beginPublicationMerge(dir, id), true);
  assert.equal(publicationDecision(dir, id), 'merge');
  assert.equal(cancelPublication(dir, id), false);
  assert.equal(publicationDecision(dir, id), 'merge');
});

test('merge commitment is crash-retry idempotent', (t) => {
  const { dir, id } = fixture(t);
  assert.equal(beginPublicationMerge(dir, id), true);
  assert.equal(beginPublicationMerge(dir, id), true);
});

test('cancellation decision is idempotent', (t) => {
  const { dir, id } = fixture(t);
  assert.equal(cancelPublication(dir, id), true);
  assert.equal(cancelPublication(dir, id), true);
});

test('invalid job ids cannot escape the decision directory', (t) => {
  const { dir } = fixture(t);
  assert.throws(() => cancelPublication(dir, '../outside'), /invalid_job_id/);
  assert.throws(() => beginPublicationMerge(dir, '/tmp/outside'), /invalid_job_id/);
});
