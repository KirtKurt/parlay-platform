import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import {
  JOB_ID,
  evaluateRequiredChecks,
  publicationDirectories,
  withinAuthorizedScope,
  writePublicationReceipt,
  readPublicationReceipt
} from '../src/publication.js';
import { updateJobFromPublisher } from '../src/publisher-job-state.js';
import { collectChanges } from '../src/git.js';
import { JobStore } from '../src/store.js';
import { sanitize } from '../src/sanitize.js';
import { loadPublicationPolicy, validatePublisherRequest, validatePullRequestIdentity, PROOF_WORKFLOW } from '../src/publication-policy.js';
import { assertMainAncestor, validatePublicationHistory, validateMergedPublication } from '../src/publication-git-guard.js';
import { beginPublicationMerge, publicationDecision } from '../src/publication-decision.js';

const exec = promisify(execFile);

function required(name) {
  const value = String(process.env[name] || '').trim();
  if (!value) throw new Error(`missing_${name.toLowerCase()}`);
  return value;
}

const policy = loadPublicationPolicy();
const repository = policy.repository;
const dataDir = required('INQSI_ENGINEERING_DATA_DIR');
const token = required('GH_TOKEN');
const [owner] = repository.split('/');
const dirs = publicationDirectories(dataDir);

function record(receipt) {
  const durable = writePublicationReceipt(dataDir, receipt);
  updateJobFromPublisher(dataDir, durable);
}

function archiveClaim(claim, id) {
  const target = path.join(dirs.processed, id);
  if (fs.existsSync(target)) fs.rmSync(claim, { recursive: true, force: true });
  else fs.renameSync(claim, target);
}

async function git(cwd, args, env = process.env, allowFailure = false) {
  try {
    const result = await exec('git', ['-c', 'core.hooksPath=/dev/null', ...args], { cwd, env, maxBuffer: 30 * 1024 * 1024, timeout: 120000 });
    return result.stdout.trim();
  } catch (error) {
    if (allowFailure) return null;
    throw new Error('publisher_git_operation_failed');
  }
}

async function withAskPass(fn) {
  const authDir = fs.mkdtempSync(path.join(os.tmpdir(), 'inqsi-publish-auth-'));
  const askPass = path.join(authDir, 'askpass.sh');
  fs.writeFileSync(askPass, '#!/bin/sh\ncase "$1" in\n  *Username*) printf "%s\\n" "x-access-token" ;;\n  *) printf "%s\\n" "$GH_TOKEN" ;;\nesac\n', { mode: 0o700 });
  try {
    return await fn({ ...process.env, GIT_ASKPASS: askPass, GIT_TERMINAL_PROMPT: '0', GH_TOKEN: token });
  } finally {
    fs.rmSync(authDir, { recursive: true, force: true });
  }
}

async function github(endpoint, init = {}) {
  const response = await fetch(`https://api.github.com/repos/${repository}${endpoint}`, {
    ...init,
    signal: AbortSignal.timeout(30000),
    headers: {
      accept: 'application/vnd.github+json',
      authorization: `Bearer ${token}`,
      'x-github-api-version': '2022-11-28',
      ...(init.headers || {})
    }
  });
  const text = await response.text();
  let body = {};
  try { body = text ? JSON.parse(text) : {}; } catch { body = {}; }
  if (!response.ok) throw new Error(`github_request_failed:${response.status}`);
  return body;
}

function claimDirectories() {
  fs.mkdirSync(dirs.outbox, { recursive: true, mode: 0o700 });
  const entries = fs.readdirSync(dirs.outbox).sort();
  const claims = [];
  for (const name of entries) {
    if (JOB_ID.test(name)) {
      const source = path.join(dirs.outbox, name);
      const claimed = path.join(dirs.outbox, `.processing-${name}`);
      try { fs.renameSync(source, claimed); claims.push(claimed); }
      catch (error) { if (error.code !== 'ENOENT' && error.code !== 'EEXIST') throw error; }
    } else if (name.startsWith('.processing-') && JOB_ID.test(name.slice('.processing-'.length))) {
      claims.push(path.join(dirs.outbox, name));
    }
  }
  return [...new Set(claims)];
}

async function findExistingPullRequest(branch) {
  const pulls = await github(`/pulls?state=all&head=${encodeURIComponent(`${owner}:${branch}`)}&base=main&per_page=20`);
  if (!Array.isArray(pulls) || pulls.length >= 20) throw new Error('publication_pr_listing_incomplete');
  const matches = pulls.filter((pr) => pr.head?.ref === branch && pr.base?.ref === 'main');
  if (matches.length > 1) throw new Error('publication_pr_identity_ambiguous');
  return matches[0] || null;
}

async function processClaim(claimDir) {
  const manifest = JSON.parse(fs.readFileSync(path.join(claimDir, 'manifest.json'), 'utf8'));
  const patch = fs.readFileSync(path.join(claimDir, 'patch.diff'), 'utf8');
  validatePublisherRequest(manifest, patch, policy);
  if (path.basename(claimDir) !== `.processing-${manifest.jobId}`) throw new Error('publication_claim_identity_mismatch');
  const previous = readPublicationReceipt(dataDir, manifest.jobId);
  if (previous?.state === 'merged' || previous?.state === 'merge_conflict') { record(previous); return true; }
  if (manifest.repository !== repository) throw new Error('publisher_repository_mismatch');

  const store = new JobStore(dataDir);
  const cancelled = () => {
    const job = store.get(manifest.jobId);
    if (!job) throw new Error('publication_job_missing');
    return publicationDecision(dataDir, manifest.jobId) === 'cancelled' || job.cancelRequested || job.status === 'cancelled';
  };
  const assertNotCancelled = () => { if (cancelled()) throw new Error('publication_cancelled'); };
  const receipt = (pr, publishedCommit, fields) => ({
    jobId: manifest.jobId,
    branch: manifest.branch,
    commit: publishedCommit,
    pullRequest: pr?.html_url,
    pullRequestNumber: pr?.number,
    patchSha256: manifest.patchSha256,
    ...fields
  });

  const checkout = fs.mkdtempSync(path.join(os.tmpdir(), 'inqsi-publication-'));
  try {
    // Discover the immutable PR before considering branch recreation. GitHub's
    // pull ref survives deletion of the source branch after a merge.
    let pr = await findExistingPullRequest(manifest.branch);
    if (pr) pr = await github(`/pulls/${pr.number}`);
    if (!pr && cancelled()) {
      record(receipt(null, null, { state: 'cancelled', reason: 'publication_cancelled', observedGitHubState: 'no_pull_request' }));
      return true;
    }

    await git(checkout, ['init']);
    await git(checkout, ['remote', 'add', 'origin', `https://github.com/${repository}.git`]);
    const refreshMain = async () => {
      await git(checkout, ['fetch', 'origin', 'refs/heads/main:refs/remotes/origin/main']);
      return git(checkout, ['rev-parse', 'refs/remotes/origin/main']);
    };
    let mainRevision = await refreshMain();
    await assertMainAncestor(checkout, manifest.startingRevision, mainRevision);
    await git(checkout, ['checkout', '-b', manifest.branch, manifest.startingRevision]);
    fs.writeFileSync(path.join(checkout, '.inqsi.patch'), patch, { mode: 0o600 });
    await git(checkout, ['apply', '--index', '--binary', '.inqsi.patch']);
    fs.rmSync(path.join(checkout, '.inqsi.patch'), { force: true });

    const applied = await collectChanges(checkout, manifest.startingRevision);
    if (JSON.stringify(applied.changedFiles) !== JSON.stringify([...manifest.changedFiles].sort())) throw new Error('publication_file_set_mismatch');
    if (applied.changedFiles.some((file) => !withinAuthorizedScope(file, manifest.authorizedScope))) throw new Error('publication_scope_violation');

    await git(checkout, ['config', 'user.name', 'InQsi Engineering Publisher']);
    await git(checkout, ['config', 'user.email', 'inqsi-engineering@users.noreply.github.com']);
    await git(checkout, ['commit', '-m', `InQsi engineering job ${manifest.jobId}`]);
    const localCommit = await git(checkout, ['rev-parse', 'HEAD']);
    const localTree = await git(checkout, ['rev-parse', 'HEAD^{tree}']);

    const validatePublishedHead = async (head) => {
      if (!/^[0-9a-f]{40}$/.test(head || '')) throw new Error('publication_head_missing');
      if (await git(checkout, ['rev-parse', `${head}^{tree}`]) !== localTree) throw new Error('publication_branch_collision');
      if (await git(checkout, ['show', '-s', '--format=%P', head]) !== manifest.startingRevision) throw new Error('publication_unexpected_parent');
    };

    let publishedCommit;
    if (pr) {
      // allowClosed only permits read/close reconciliation, never a merge.
      validatePullRequestIdentity(pr, manifest, pr.head?.sha, { allowClosed: true });
      await git(checkout, ['fetch', 'origin', `refs/pull/${pr.number}/head`]);
      publishedCommit = await git(checkout, ['rev-parse', 'FETCH_HEAD']);
      validatePullRequestIdentity(pr, manifest, publishedCommit, { allowClosed: true });
      await validatePublishedHead(publishedCommit);
    } else {
      await validatePublicationHistory(checkout, manifest, localCommit, mainRevision, policy);
      await withAskPass(async (env) => {
        const remote = await git(checkout, ['ls-remote', '--heads', 'origin', `refs/heads/${manifest.branch}`], env);
        if (remote) {
          await git(checkout, ['fetch', 'origin', `refs/heads/${manifest.branch}`], env);
          publishedCommit = await git(checkout, ['rev-parse', 'FETCH_HEAD'], env);
          await validatePublishedHead(publishedCommit);
        } else {
          assertNotCancelled();
          await git(checkout, ['push', 'origin', `HEAD:refs/heads/${manifest.branch}`], env);
          publishedCommit = localCommit;
        }
      });
      assertNotCancelled();
      pr = await github('/pulls', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          title: `InQsi Engineering job ${manifest.jobId}`,
          head: manifest.branch,
          base: 'main',
          body: `Validated Engineering Console job ${manifest.jobId}.\n\nAuthorized scope: ${manifest.authorizedScope.join(', ')}\nStarting revision: ${manifest.startingRevision}\nPatch SHA-256: ${manifest.patchSha256}`
        })
      });
    }

    const verifyChecks = async (observedPr) => {
      const checks = await github(`/commits/${publishedCommit}/check-runs?per_page=100`);
      const trustedChecks = (checks.check_runs || []).filter((run) => run.head_sha === publishedCommit && run.app?.slug === 'github-actions');
      const evaluation = evaluateRequiredChecks(trustedChecks, policy.requiredChecks);
      if (evaluation.state !== 'passed') {
        record(receipt(observedPr, publishedCommit, {
          state: evaluation.state === 'pending' ? 'checks_pending' : 'checks_failed',
          reason: evaluation.reason,
          observedGitHubState: observedPr.merged_at ? 'merged' : observedPr.state
        }));
        return false;
      }
      for (const name of policy.requiredChecks) {
        const check = trustedChecks.filter((item) => item.name === name).sort((a, b) => b.id - a.id)[0];
        const match = String(check?.details_url || '').match(/^https:\/\/github\.com\/KirtKurt\/parlay-platform\/actions\/runs\/([0-9]+)(?:\/|$)/);
        if (!match) throw new Error('publication_check_provenance_missing');
        const run = await github(`/actions/runs/${match[1]}`);
        if (run.path !== PROOF_WORKFLOW || run.event !== 'pull_request' || run.head_sha !== publishedCommit || run.head_repository?.full_name !== repository || run.status !== 'completed' || run.conclusion !== 'success' || !(run.pull_requests || []).some((item) => item.number === observedPr.number)) throw new Error('publication_check_provenance_mismatch');
      }
      return true;
    };

    const finishMerged = async (observedPr, expectedMerge = null) => {
      validatePullRequestIdentity(observedPr, manifest, publishedCommit);
      if (observedPr.state !== 'closed' || !observedPr.merged_at || !/^[0-9a-f]{40}$/.test(observedPr.merge_commit_sha || '')) throw new Error('publication_merge_commit_missing');
      if (expectedMerge && observedPr.merge_commit_sha !== expectedMerge) throw new Error('publication_merge_response_mismatch');
      await git(checkout, ['fetch', 'origin', observedPr.merge_commit_sha]);
      mainRevision = await refreshMain();
      await validateMergedPublication(checkout, manifest, publishedCommit, observedPr.merge_commit_sha, mainRevision, policy);

      // After the landed commit is cryptographically/history validated, make one
      // durable first-writer choice between accepted cancellation and merge.
      // This closes the gap where cancellation could win after a read but before
      // a terminal receipt. A verified landed merge that conflicts with an
      // already accepted cancellation is a terminal reconciliation condition and
      // must not be hidden by pending/failed required checks.
      const priorDecision = publicationDecision(dataDir, manifest.jobId);
      try {
        beginPublicationMerge(dataDir, manifest.jobId);
      } catch (error) {
        if (error?.message !== 'publication_cancelled') throw error;
        record(receipt(observedPr, publishedCommit, {
          state: 'merge_conflict',
          mergeCommit: observedPr.merge_commit_sha,
          observedGitHubState: 'merged',
          verificationMain: mainRevision,
          completionMode: 'manual_reconciliation_required',
          reason: 'verified_merge_with_accepted_cancellation',
          cancelRequested: true
        }));
        return true;
      }

      if (!await verifyChecks(observedPr)) return false;
      record(receipt(observedPr, publishedCommit, {
        state: 'merged',
        mergeCommit: observedPr.merge_commit_sha,
        observedGitHubState: 'merged',
        verificationMain: mainRevision,
        // A pre-existing durable merge commitment is enough to distinguish a
        // lost-response recovery from a first observation of an external merge,
        // without claiming which GitHub actor actually completed the merge.
        completionMode: expectedMerge ? 'autonomous_verified' : priorDecision === 'merge' ? 'publisher_commitment_recovered' : 'external_verified'
      }));
      return true;
    };

    const finishCancellation = async () => {
      let latest = await github(`/pulls/${pr.number}`);
      validatePullRequestIdentity(latest, manifest, publishedCommit, { allowClosed: true });
      if (latest.merged_at) return await finishMerged(latest);
      if (latest.state === 'open') {
        // A rejected/ambiguous close is not proof of cancellation. Preserve the
        // claim and re-read the outcome, including a concurrent external merge.
        let closeError;
        try {
          await github(`/pulls/${pr.number}`, {
            method: 'PATCH',
            headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ state: 'closed' })
          });
        } catch (error) { closeError = error; }
        latest = await github(`/pulls/${pr.number}`);
        validatePullRequestIdentity(latest, manifest, publishedCommit, { allowClosed: true });
        if (latest.merged_at) return await finishMerged(latest);
        if (latest.state !== 'closed') throw closeError || new Error('publication_cancellation_unconfirmed');
      }
      record(receipt(latest, publishedCommit, { state: 'cancelled', reason: 'publication_cancelled', observedGitHubState: 'closed' }));
      return true;
    };

    pr = await github(`/pulls/${pr.number}`);
    validatePullRequestIdentity(pr, manifest, publishedCommit, { allowClosed: true });
    if (pr.merged_at) return await finishMerged(pr);
    if (cancelled()) return await finishCancellation();
    validatePullRequestIdentity(pr, manifest, publishedCommit);
    mainRevision = await refreshMain();
    await validatePublicationHistory(checkout, manifest, publishedCommit, mainRevision, policy);
    if (!await verifyChecks(pr)) return false;

    pr = await github(`/pulls/${pr.number}`);
    validatePullRequestIdentity(pr, manifest, publishedCommit, { allowClosed: true });
    if (pr.merged_at) return await finishMerged(pr);
    if (cancelled()) return await finishCancellation();
    validatePullRequestIdentity(pr, manifest, publishedCommit);
    assertNotCancelled();
    beginPublicationMerge(dataDir, manifest.jobId);

    let merge;
    try {
      merge = await github(`/pulls/${pr.number}/merge`, {
        method: 'PUT',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ sha: publishedCommit, merge_method: 'merge', commit_title: `InQsi Engineering job ${manifest.jobId}` })
      });
    } catch (error) {
      const latest = await github(`/pulls/${pr.number}`);
      if (latest.merged_at) return await finishMerged(latest);
      throw error;
    }
    if (merge.merged !== true || !/^[0-9a-f]{40}$/.test(merge.sha || '')) throw new Error('publication_merge_rejected');
    return await finishMerged(await github(`/pulls/${pr.number}`), merge.sha);
  } finally {
    fs.rmSync(checkout, { recursive: true, force: true });
  }
}

fs.mkdirSync(dirs.processed, { recursive: true, mode: 0o700 });
let failed = false;
for (const claim of claimDirectories()) {
  const id = path.basename(claim).replace(/^\.processing-/, '');
  try {
    const complete = await processClaim(claim);
    if (complete) archiveClaim(claim, id);
  } catch (error) {
    const reason = String(error?.message || error);
    if (reason === 'publication_cancelled') {
      // Cancellation can win immediately before commitment. Keep the request
      // retriable until the publisher verifies the actual GitHub outcome.
      record({ jobId: id, state: 'cancellation_pending', reason });
      continue;
    }
    failed = true;
    record({ jobId: id, state: 'publisher_failed', reason: sanitize(reason).slice(0, 300) });
  }
}
if (failed) process.exitCode = 1;
