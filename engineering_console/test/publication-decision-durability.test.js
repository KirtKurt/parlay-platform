import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { cancelPublication, publicationDecision } from '../src/publication-decision.js';
import { publicationDirectories } from '../src/publication.js';

const id = '11111111-1111-4111-8111-111111111111';

function fixture(t) {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'eng-console-decision-'));
  t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
  const root = path.join(path.dirname(publicationDirectories(dataDir).outbox), 'publication-decisions');
  fs.mkdirSync(root, { recursive: true, mode: 0o700 });
  return { dataDir, root };
}

test('orphaned private staging record is never observed as the durable decision', (t) => {
  const { dataDir, root } = fixture(t);
  const staging = path.join(root, `.${id}.interrupted.tmp`);
  fs.writeFileSync(staging, '', { mode: 0o600 });
  assert.equal(publicationDecision(dataDir, id), null);
  assert.equal(cancelPublication(dataDir, id), true);
  assert.equal(publicationDecision(dataDir, id), 'cancelled');
  assert.equal(fs.readFileSync(staging, 'utf8'), '');
});

test('published decision is a complete private regular file', (t) => {
  const { dataDir, root } = fixture(t);
  assert.equal(cancelPublication(dataDir, id), true);
  const target = path.join(root, id);
  const stat = fs.lstatSync(target);
  assert.equal(stat.isFile(), true);
  assert.equal(stat.mode & 0o077, 0);
  assert.equal(fs.readFileSync(target, 'utf8'), 'cancelled\n');
});
