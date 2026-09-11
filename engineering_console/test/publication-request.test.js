import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { publicationDirectories, writePublicationRequest } from '../src/publication.js';

test('publication request is durable and idempotent for the same patch', () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'inqsi-publication-'));
  const config = { dataDir, requiredChecks: ['build'] };
  const job = {
    id: '11111111-1111-4111-8111-111111111111',
    repository: 'KirtKurt/parlay-platform',
    startingRevision: 'a'.repeat(40),
    authorizedScope: ['engineering_console'],
    changedFiles: ['engineering_console/README.md']
  };
  const patch = 'diff --git a/engineering_console/README.md b/engineering_console/README.md\n';
  const first = writePublicationRequest(config, job, patch);
  const second = writePublicationRequest(config, job, patch);
  assert.equal(first.patchSha256, second.patchSha256);
  const { outbox } = publicationDirectories(dataDir);
  assert.equal(fs.existsSync(path.join(outbox, job.id, 'manifest.json')), true);
  assert.throws(() => writePublicationRequest(config, job, `${patch}+different\n`), /collision/);
});
