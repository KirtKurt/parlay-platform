import test from 'node:test';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { evaluateRequiredChecks, patchContainsCredential, publicationDirectories, sha256, validatePublicationManifest, writePublicationRequest } from '../src/publication.js';
import { redact, sanitizeValue, publicJob } from '../src/sanitize.js';

function fixture(t) {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'inqsi-review-regression-'));
  t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
  const job = { id: crypto.randomUUID(), repository: 'KirtKurt/parlay-platform', startingRevision: 'a'.repeat(40), authorizedScope: ['docs/engineering-console-proof'], changedFiles: ['docs/engineering-console-proof/check.txt'], cancelRequested: false };
  return { config: { dataDir, requiredChecks: ['console-proof'] }, job, patch: '+Controlled Console proof\n', dirs: publicationDirectories(dataDir) };
}
function check(conclusion, id = 1) { return { name: 'console-proof', id, status: 'completed', conclusion }; }

const syntheticFineGrained = () => ['github', 'pat', 'x'.repeat(30)].join('_');
const syntheticClassic = () => `ghp_${'x'.repeat(30)}`;

test('bare fine-grained credential shape is rejected from generated patches', () => {
  assert.equal(patchContainsCredential(`+${syntheticFineGrained()}\n`), true);
});
test('fine-grained credential shape is rejected when validating the manifest', (t) => {
  const { config, job, patch } = fixture(t);
  const manifest = writePublicationRequest(config, job, patch);
  const tainted = `+${syntheticFineGrained()}\n`;
  assert.throws(() => validatePublicationManifest({ ...manifest, patchSha256: sha256(tainted) }, tainted), /publication_patch_secret_detected/);
});
test('fine-grained shapes are redacted from nested durable fields', () => {
  const result = sanitizeValue({ logs: [syntheticFineGrained()], nested: { diff: syntheticFineGrained() } });
  assert.equal(JSON.stringify(result).includes(syntheticFineGrained()), false);
});
test('fine-grained shapes are redacted from public fields', () => {
  assert.equal(JSON.stringify(publicJob({ error: syntheticFineGrained() })).includes(syntheticFineGrained()), false);
});
test('classic GitHub credential shapes are redacted from bare output', () => {
  assert.equal(redact(syntheticClassic()).includes(syntheticClassic()), false);
});
test('credential-free prose is not altered', () => {
  assert.equal(redact('Controlled Console proof'), 'Controlled Console proof');
  assert.equal(patchContainsCredential('+Controlled Console proof\n'), false);
});
test('successful required check passes', () => {
  assert.equal(evaluateRequiredChecks([check('success')], ['console-proof']).state, 'passed');
});
for (const conclusion of ['skipped', 'neutral', 'failure', 'cancelled', 'timed_out', null]) {
  test(`required check conclusion ${String(conclusion)} does not authorize merge`, () => {
    assert.equal(evaluateRequiredChecks([check(conclusion)], ['console-proof']).state, 'failed');
  });
}
test('missing and unfinished checks remain pending', () => {
  assert.equal(evaluateRequiredChecks([], ['console-proof']).state, 'pending');
  assert.equal(evaluateRequiredChecks([{ ...check('success'), status: 'in_progress' }], ['console-proof']).state, 'pending');
});
test('newer failed rerun cannot be hidden behind earlier success', () => {
  assert.equal(evaluateRequiredChecks([check('success'), check('failure', 2)], ['console-proof']).state, 'failed');
});
test('empty required-check policy fails closed', () => {
  assert.equal(evaluateRequiredChecks([], []).state, 'failed');
  assert.equal(evaluateRequiredChecks([{ name: '', id: 1, status: 'completed', conclusion: 'success' }], ['']).state, 'failed');
});
test('orphaned temp directory from a prior identical PID does not block retry', (t) => {
  const { config, job, patch, dirs } = fixture(t);
  fs.mkdirSync(dirs.outbox, { recursive: true });
  const orphan = path.join(dirs.outbox, `.${job.id}.${process.pid}.tmp`);
  fs.mkdirSync(orphan);
  fs.writeFileSync(path.join(orphan, 'unrelated-state'), 'preserve');
  writePublicationRequest(config, job, patch);
  assert.equal(fs.readFileSync(path.join(dirs.outbox, job.id, 'patch.diff'), 'utf8'), patch);
  assert.equal(fs.readFileSync(path.join(orphan, 'unrelated-state'), 'utf8'), 'preserve');
});
test('ordinary identical publication retry remains idempotent', (t) => {
  const { config, job, patch } = fixture(t);
  assert.deepEqual(writePublicationRequest(config, job, patch), writePublicationRequest(config, job, patch));
});
test('cancelled job cannot create a new visible publication request', (t) => {
  const { config, job, patch, dirs } = fixture(t);
  job.cancelRequested = true;
  assert.throws(() => writePublicationRequest(config, job, patch), /publication_cancelled/);
  assert.equal(fs.existsSync(path.join(dirs.outbox, job.id)), false);
});
test('aborted signal cannot create a new visible publication request', (t) => {
  const { config, job, patch, dirs } = fixture(t);
  const controller = new AbortController();
  controller.abort();
  assert.throws(() => writePublicationRequest(config, job, patch, controller.signal), /publication_cancelled/);
  assert.equal(fs.existsSync(path.join(dirs.outbox, job.id)), false);
});
test('cancellation immediately before atomic rename cleans only its own staging directory', (t) => {
  const { config, job, patch, dirs } = fixture(t);
  const controller = new AbortController();
  const write = fs.writeFileSync;
  const mock = t.mock.method(fs, 'writeFileSync', (...args) => {
    const result = write(...args);
    if (String(args[0]).endsWith('manifest.json')) controller.abort();
    return result;
  });
  try {
    assert.throws(() => writePublicationRequest(config, job, patch, controller.signal), /publication_cancelled/);
    assert.equal(fs.existsSync(path.join(dirs.outbox, job.id)), false);
    assert.deepEqual(fs.readdirSync(dirs.outbox), []);
  } finally { mock.mock.restore(); }
});
test('writer rejects an invalid manifest before making it visible', (t) => {
  const { config, job, patch, dirs } = fixture(t);
  job.repository = 'unexpected/repository';
  assert.throws(() => writePublicationRequest(config, job, patch), /unexpected_publication_repository/);
  assert.equal(fs.existsSync(path.join(dirs.outbox, job.id)), false);
});
test('existing request with changed required-check policy is a collision', (t) => {
  const { config, job, patch } = fixture(t);
  writePublicationRequest(config, job, patch);
  assert.throws(() => writePublicationRequest({ ...config, requiredChecks: ['different-check'] }, job, patch), /publication_request_collision/);
});
