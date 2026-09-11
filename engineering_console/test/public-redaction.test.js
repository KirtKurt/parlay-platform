import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { JobStore } from '../src/store.js';
import { publicJob } from '../src/sanitize.js';

test('durable execution strings remain complete while public strings are bounded', () => {
  const store = new JobStore(fs.mkdtempSync(path.join(os.tmpdir(), 'inqsi-public-redaction-')));
  const instruction = `token=not-a-real-secret ${'x'.repeat(15000)}`;
  const job = store.create({ instruction, authorizedScope: ['engineering_console'] }, 'owner', 'a'.repeat(40));
  const persisted = store.get(job.id);
  assert.equal(persisted.instruction.length > 12000, true);
  assert.equal(persisted.instruction.includes('not-a-real-secret'), false);
  const exposed = publicJob(persisted);
  assert.equal(exposed.instruction.length, 12000);
  assert.equal(exposed.instruction.includes('not-a-real-secret'), false);
});
