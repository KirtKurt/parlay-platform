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
import { beginPublicationMerge } from '../src/publication-decision.js';

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
  return pulls.find((pr) => pr.head?.ref === branch && pr.base?.ref === 'main') || null;
}

async function processClaim(claimDir) {
  const manifestPath = path.join(claimDir, 'manifest.json');
  const patchPath = path.join(claimDir, 'patch.diff');
  const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  const patch = fs.readFileSync(patchPath, 'utf8');
  validatePublisherRequest(manifest, patch, policy);
  if (path.basename(claimDir) !== `.processing-${manifest.jobId}`) throw new Error('publication_claim_identity_mismatch');
  const previous = readPublicationReceipt(dataDir, manifest.jobId);
  if (previous?.state === 'merged' || previous?.state === 'cancelled') { record(previous); return true; }
  const assertNotCancelled = () => {
    const job = new JobStore(dataDir).get(manifest.jobId);
    if (!job || job.cancelRequested || job.status === 'cancelled') throw new Error('publication_cancelled');
  };
  assertNotCancelled();
  if (manifest.repository !== repository) throw new Error('publisher_repository_mismatch');

  const checkout = fs.mkdtempSync(path.join(os.tmpdir(), 'inqsi-publication-'));
  try {
    await git(checkout, ['init']);
    await git(checkout, ['remote', 'add', 'origin', `https://github.com/${repository}.git`]);
    await git(checkout, ['fetch', 'origin', 'refs/heads/main:refs/remotes/origin/main']);
    let mainRevision = await git(checkout, ['rev-parse', 'refs/remotes/origin/main']);
    await assertMainAncestor(checkout, manifest.startingRevision, mainRevision);
    await git(checkout, ['checkout', '-b', manifest.branch, manifest.startingRevision]);
    fs.writeFileSync(path.join(checkout, '.inqsi.patch'), patch, { mode: 0o600 });
    await git(checkout, ['apply', '--index', '--binary', '.inqsi.patch']);
    fs.rmSync(path.join(checkout, '.inqsi.patch'), { force: true });

    const applied = await collectChanges(checkout, manifest.startingRevision);
    const expected = [...manifest.changedFiles].sort();
    if (JSON.stringify(applied.changedFiles) !== JSON.stringify(expected)) throw new Error('publication_file_set_mismatch');
    if (applied.changedFiles.some((file) => !withinAuthorizedScope(file, manifest.authorizedScope))) throw new Error('publication_scope_violation');

    await git(checkout, ['config', 'user.name', 'InQsi Engineering Publisher']);
    await git(checkout, ['config', 'user.email', 'inqsi-engineering@users.noreply.github.com']);
    await git(checkout, ['commit', '-m', `InQsi engineering job ${manifest.jobId}`]);
    const localCommit = await git(checkout, ['rev-parse', 'HEAD']);
    const localTree = await git(checkout, ['rev-parse', 'HEAD^{tree}']);
    await validatePublicationHistory(checkout, manifest, localCommit, mainRevision, policy);

    let publishedCommit = null;
    await withAskPass(async (env) => {
      const remote = await git(checkout, ['ls-remote', '--heads', 'origin', `refs/heads/${manifest.branch}`], env);
      if (remote) {
        await git(checkout, ['fetch', 'origin', `refs/heads/${manifest.branch}`], env);
        const remoteTree = await git(checkout, ['rev-parse', 'FETCH_HEAD^{tree}'], env);
        if (remoteTree !== localTree) throw new Error('publication_branch_collision');
        publishedCommit = await git(checkout, ['rev-parse', 'FETCH_HEAD'], env);
        const parents = await git(checkout, ['show', '-s', '--format=%P', publishedCommit], env);
        if (parents !== manifest.startingRevision) throw new Error('publication_unexpected_parent');
      } else {
        assertNotCancelled();
        await git(checkout, ['push', 'origin', `HEAD:refs/heads/${manifest.branch}`], env);
        publishedCommit = localCommit;
      }
    });

    let pr = await findExistingPullRequest(manifest.branch);
    if (pr && !pr.merged_at && pr.state !== 'open') throw new Error('publication_pr_closed_without_merge');
    if (!pr) {
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

    pr = await github(`/pulls/${pr.number}`);
    validatePullRequestIdentity(pr, manifest, publishedCommit);
    await git(checkout, ['fetch', 'origin', 'refs/heads/main:refs/remotes/origin/main']);
    mainRevision = await git(checkout, ['rev-parse', 'refs/remotes/origin/main']);
    if (pr.merged_at) {
      if (!/^[0-9a-f]{40}$/i.test(pr.merge_commit_sha || '')) throw new Error('publication_merge_commit_missing');
      await git(checkout, ['fetch', 'origin', pr.merge_commit_sha]);
      await validateMergedPublication(checkout, manifest, publishedCommit, pr.merge_commit_sha, mainRevision, policy);
    } else {
      await validatePublicationHistory(checkout, manifest, publishedCommit, mainRevision, policy);
    }

    const checks = await github(`/commits/${publishedCommit}/check-runs?per_page=100`);
    const trustedChecks = (checks.check_runs || []).filter((run) => run.head_sha === publishedCommit && run.app?.slug === 'github-actions');
    const evaluation = evaluateRequiredChecks(trustedChecks, policy.requiredChecks);
    if (evaluation.state === 'pending') {
      record({ jobId: manifest.jobId, state: 'checks_pending', reason: evaluation.reason, branch: manifest.branch, commit: publishedCommit, pullRequest: pr.html_url, pullRequestNumber: pr.number, patchSha256: manifest.patchSha256 });
      return false;
    }
    if (evaluation.state === 'failed') {
      record({ jobId: manifest.jobId, state: 'checks_failed', reason: evaluation.reason, branch: manifest.branch, commit: publishedCommit, pullRequest: pr.html_url, pullRequestNumber: pr.number, patchSha256: manifest.patchSha256 });
      return false;
    }

    for (const name of policy.requiredChecks) {
      const check = trustedChecks.filter((item) => item.name === name).sort((a, b) => b.id - a.id)[0];
      const match = String(check?.details_url || '').match(/^https:\/\/github\.com\/KirtKurt\/parlay-platform\/actions\/runs\/([0-9]+)(?:\/|$)/);
      if (!match) throw new Error('publication_check_provenance_missing');
      const run = await github(`/actions/runs/${match[1]}`);
      if (run.path !== PROOF_WORKFLOW || run.event !== 'pull_request' || run.head_sha !== publishedCommit || run.head_repository?.full_name !== repository || run.status !== 'completed' || run.conclusion !== 'success' || !(run.pull_requests || []).some((item) => item.number === pr.number)) throw new Error('publication_check_provenance_mismatch');
    }
    if (pr.merged_at) {
      record({ jobId: manifest.jobId, state: 'merged', branch: manifest.branch, commit: publishedCommit, pullRequest: pr.html_url, pullRequestNumber: pr.number, mergeCommit: pr.merge_commit_sha, patchSha256: manifest.patchSha256 });
      return true;
    }

    pr = await github(`/pulls/${pr.number}`);
    validatePullRequestIdentity(pr, manifest, publishedCommit);
    assertNotCancelled();
    beginPublicationMerge(dataDir, manifest.jobId);
    const merge = await github(`/pulls/${pr.number}/merge`, {
      method: 'PUT',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ sha: publishedCommit, merge_method: 'merge', commit_title: `InQsi Engineering job ${manifest.jobId}` })
    });
    if (!merge.merged) throw new Error('publication_merge_rejected');
    record({ jobId: manifest.jobId, state: 'merged', branch: manifest.branch, commit: publishedCommit, pullRequest: pr.html_url, pullRequestNumber: pr.number, mergeCommit: merge.sha, patchSha256: manifest.patchSha256 });
    return true;
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
      try {
        const pr = await findExistingPullRequest(`inqsi/publish-${id}`);
        if (pr?.state === 'open' && !pr.merged_at) await github(`/pulls/${pr.number}`, { method: 'PATCH', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ state: 'closed' }) });
      } catch { /* cancellation remains authoritative even if PR cleanup is unavailable */ }
      record({ jobId: id, state: 'cancelled', reason: 'publication_cancelled' });
      archiveClaim(claim, id);
      continue;
    }
    failed = true;
    record({ jobId: id, state: 'publisher_failed', reason: sanitize(reason).slice(0, 300) });
  }
}
if (failed) process.exitCode = 1;
