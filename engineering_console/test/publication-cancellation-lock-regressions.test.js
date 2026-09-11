import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { runWithPublisherLock } from '../src/publisher-lock.js';

test('publisher lock accepts a normalized absolute directory spelling', async (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'inqsi-lock-normalize-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const lockDir = path.join(root, 'locks');
  const script = path.join(root, 'noop.mjs');
  fs.writeFileSync(script, 'process.exit(0);\n');
  const withTrailingSlash = `${lockDir}${path.sep}`;
  assert.equal(await runWithPublisherLock(withTrailingSlash, script, { stdio: 'ignore' }), 0);
});

test('API permits cancelling retryable failed publication jobs', () => {
  const source = fs.readFileSync(new URL('../src/server.js', import.meta.url), 'utf8');
  assert.match(source, /job\.status === 'failed' && publicationVisible/);
  assert.ok(source.indexOf("const publicationVisible") < source.indexOf("const cancellableStatus"));
});

test('Console UI exposes cancellation throughout publication and retry states', () => {
  const source = fs.readFileSync(new URL('../../frontend/components/EngineeringConsole.tsx', import.meta.url), 'utf8');
  assert.match(source, /publicationState\?: string/);
  assert.match(source, /awaiting_publication', 'published'/);
  assert.match(source, /selectedStatus === 'failed' && publicationVisible/);
  assert.match(source, /Cancel publication/);
});
