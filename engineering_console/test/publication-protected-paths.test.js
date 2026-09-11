import test from 'node:test';
import assert from 'node:assert/strict';
import { isProtectedPublicationPath, validatePublicationManifest, sha256 } from '../src/publication.js';

test('autonomous publication cannot modify its own trust boundary', () => {
  assert.equal(isProtectedPublicationPath('.github/workflows/frontend-build.yml'), true);
  assert.equal(isProtectedPublicationPath('engineering_console/src/server.js'), true);
  assert.equal(isProtectedPublicationPath('frontend/app/api/engineering/route.js'), true);
  assert.equal(isProtectedPublicationPath('frontend/app/page.tsx'), false);
  assert.equal(isProtectedPublicationPath('hello_world/model.py'), false);
});

test('publisher rejects protected paths even if an authorized scope contains them', () => {
  const patch = 'diff --git a/engineering_console/src/server.js b/engineering_console/src/server.js\n';
  const manifest = {
    version: 1,
    jobId: '11111111-1111-4111-8111-111111111111',
    repository: 'KirtKurt/parlay-platform',
    startingRevision: 'a'.repeat(40),
    branch: 'inqsi/publish-11111111-1111-4111-8111-111111111111',
    authorizedScope: ['engineering_console'],
    changedFiles: ['engineering_console/src/server.js'],
    requiredChecks: ['build'],
    patchSha256: sha256(patch)
  };
  assert.throws(() => validatePublicationManifest(manifest, patch), /protected_path/);
});
