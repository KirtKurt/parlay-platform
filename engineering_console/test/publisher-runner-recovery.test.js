import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { validatePullRequestIdentity, PROOF_ROOT } from '../src/publication-policy.js';
import { writePublicationReceipt, readPublicationReceipt } from '../src/publication.js';
import { updateJobFromPublisher } from '../src/publisher-job-state.js';
import { cancelPublication, beginPublicationMerge } from '../src/publication-decision.js';
import { JobStore } from '../src/store.js';

const id = '11111111-1111-4111-8111-111111111111';
const head = 'b'.repeat(40);
const manifest = {
  version: 1,
  jobId: id,
  repository: 'KirtKurt/parlay-platform',
  branch: `inqsi/publish-${id}`,
  startingRevision: 'a'.repeat(40),
  authorizedScope: [PROOF_ROOT],
  changedFiles: [`${PROOF_ROOT}/proof.md`],
  requiredChecks: ['engineering-console-publication-proof'],
  patchSha256: 'c'.repeat(64)
};
const pr = (state = 'open', merged_at = null) => ({
  number: 10,
  state,
  draft: false,
  merged_at,
  merge_commit_sha: merged_at ? 'd'.repeat(40) : null,
  head: { ref: manifest.branch, sha: head, repo: { full_name: manifest.repository } },
  base: { ref: 'main', repo: { full_name: manifest.repository } }
});
const temporary = (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'inqsi-publisher-recovery-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  return dir;
};

test('closed unmerged PR requires explicit reconciliation mode', () => {
  assert.throws(() => validatePullRequestIdentity(pr('closed'), manifest, head), /closed_without_merge/);
  assert.doesNotThrow(() => validatePullRequestIdentity(pr('closed'), manifest, head, { allowClosed: true }));
});

test('merged closed PR remains a valid immutable identity', () => {
  assert.doesNotThrow(() => validatePullRequestIdentity(pr('closed', '2026-09-11T20:00:00Z'), manifest, head));
});

test('unexpected PR state fails closed even in reconciliation mode', () => {
  assert.throws(() => validatePullRequestIdentity(pr('unknown'), manifest, head, { allowClosed: true }), /state_invalid/);
});

test('verified merge conflict receipt cannot be downgraded later', (t) => {
  const dir = temporary(t);
  const conflict = writePublicationReceipt(dir, { jobId: id, state: 'merge_conflict', reason: 'verified_external_merge_with_accepted_cancellation' });
  assert.deepEqual(writePublicationReceipt(dir, { jobId: id, state: 'merged' }), conflict);
  assert.deepEqual(readPublicationReceipt(dir, id), conflict);
});

test('merge conflict blocks the durable job instead of claiming completion', (t) => {
  const dir = temporary(t);
  const store = new JobStore(dir);
  const job = store.create({ instruction: 'Proof', authorizedScope: [PROOF_ROOT] }, 'owner', 'a'.repeat(40));
  updateJobFromPublisher(dir, {
    jobId: job.id,
    state: 'merge_conflict',
    reason: 'verified_external_merge_with_accepted_cancellation',
    observedGitHubState: 'merged',
    completionMode: 'manual_reconciliation_required',
    mergeCommit: 'd'.repeat(40)
  });
  const saved = store.get(job.id);
  assert.equal(saved.status, 'blocked');
  assert.equal(saved.publicationState, 'merge_conflict');
  assert.equal(saved.observedGitHubState, 'merged');
  assert.equal(saved.cancelRequested, true);
});

test('durable cancellation still prevents later merge commitment', (t) => {
  const dir = temporary(t);
  assert.equal(cancelPublication(dir, id), true);
  assert.throws(() => beginPublicationMerge(dir, id), /publication_cancelled/);
});

test('publisher discovers existing PR before branch recreation and uses immutable pull ref', () => {
  const source = fs.readFileSync(new URL('../scripts/publisher-runner.mjs', import.meta.url), 'utf8');
  const discover = source.indexOf('let pr = await findExistingPullRequest(manifest.branch)');
  const remoteBranch = source.indexOf("['ls-remote', '--heads', 'origin'");
  assert.ok(discover >= 0 && remoteBranch > discover);
  assert.match(source, /refs\/pull\/\$\{pr\.number\}\/head/);
  assert.match(source, /publication_pr_listing_incomplete/);
  assert.match(source, /publication_pr_identity_ambiguous/);
});

test('publisher validates landed merge before durable merged receipt', () => {
  const source = fs.readFileSync(new URL('../scripts/publisher-runner.mjs', import.meta.url), 'utf8');
  const finish = source.indexOf('const finishMerged = async');
  const validate = source.indexOf('await validateMergedPublication', finish);
  const record = source.indexOf('record(receipt(observedPr, publishedCommit', finish);
  assert.ok(finish >= 0 && validate > finish && record > validate);
  assert.match(source, /return await finishMerged\(await github\(`\/pulls\/\$\{pr\.number\}`\), merge\.sha\)/);
  assert.match(source, /if \(latest\.merged_at\) return await finishMerged\(latest\)/);
});

test('cancellation remains pending until GitHub outcome is verified', () => {
  const source = fs.readFileSync(new URL('../scripts/publisher-runner.mjs', import.meta.url), 'utf8');
  assert.match(source, /state: 'cancellation_pending'/);
  assert.match(source, /publication_cancellation_unconfirmed/);
  assert.match(source, /state: conflict \? 'merge_conflict' : 'merged'/);
  assert.match(source, /manual_reconciliation_required/);
});
