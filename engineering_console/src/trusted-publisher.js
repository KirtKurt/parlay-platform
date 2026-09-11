import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { JobStore } from './store.js';
import { collectChanges } from './git.js';
import { patchContainsCredential } from './publish-bundle.js';
import { sanitize } from './sanitize.js';

const exec = promisify(execFile);
const JOB_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const SHA = /^[0-9a-f]{40}$/i;

function sleep(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }
function hash(value) { return crypto.createHash('sha256').update(value).digest('hex'); }
function normalize(value) { return String(value || '').replaceAll('\\', '/').replace(/^\.\//, '').replace(/\/$/, ''); }
function within(file, scopes) { const value = normalize(file); return Boolean(value) && !value.startsWith('/') && !value.split('/').some((part) => !part || part === '..' || part === '.git') && scopes.some((scope) => value === normalize(scope) || value.startsWith(`${normalize(scope)}/`)); }

export function validateManifest(manifest, patch) {
  if (manifest?.version !== 1 || !JOB_ID.test(manifest.jobId) || !SHA.test(manifest.startingRevision)) throw new Error('invalid_publish_manifest');
  if (manifest.repository !== 'KirtKurt/parlay-platform') throw new Error('unexpected_publish_repository');
  if (manifest.proposedBranch !== `inqsi/job-${manifest.jobId}`) throw new Error('invalid_publish_branch');
  if (!Array.isArray(manifest.authorizedScope) || !manifest.authorizedScope.length) throw new Error('missing_publish_scope');
  if (!Array.isArray(manifest.changedFiles) || !manifest.changedFiles.length) throw new Error('missing_publish_changes');
  if (manifest.changedFiles.some((file) => !within(file, manifest.authorizedScope))) throw new Error('publish_scope_violation');
  if (hash(patch) !== manifest.patchSha256) throw new Error('publish_patch_hash_mismatch');
  if (patchContainsCredential(patch)) throw new Error('publish_patch_secret_detected');
  return true;
}

export function checksAllowMerge(checkRuns) {
  if (!Array.isArray(checkRuns) || checkRuns.length === 0) return false;
  return checkRuns.every((run) => run.status === 'completed' && ['success', 'neutral', 'skipped'].includes(run.conclusion));
}

function publisherConfig(env = process.env) {
  const dataDir = env.INQSI_ENGINEERING_DATA_DIR;
  const token = env.INQSI_ENGINEERING_GITHUB_TOKEN;
  if (!dataDir || !path.isAbsolute(dataDir) || !token) throw new Error('trusted_publisher_configuration_missing');
  return {
    dataDir: path.resolve(dataDir),
    outboxDir: path.join(path.resolve(dataDir), 'publish-outbox'),
    processedDir: path.join(path.resolve(dataDir), 'publish-processed'),
    repository: env.INQSI_ENGINEERING_GITHUB_REPOSITORY || 'KirtKurt/parlay-platform',
    baseBranch: env.INQSI_ENGINEERING_GITHUB_BASE_BRANCH || 'main',
    token,
    autoMerge: String(env.INQSI_ENGINEERING_AUTO_MERGE || 'true').toLowerCase() === 'true',
    pollMs: Math.max(5000, Number(env.INQSI_ENGINEERING_PUBLISH_POLL_MS || 15000)),
    checkTimeoutMs: Math.max(60000, Number(env.INQSI_ENGINEERING_CHECK_TIMEOUT_MS || 1800000))
  };
}

async function github(config, endpoint, init = {}) {
  const response = await fetch(`https://api.github.com/repos/${config.repository}${endpoint}`, {
    ...init,
    headers: {
      accept: 'application/vnd.github+json',
      authorization: `Bearer ${config.token}`,
      'x-github-api-version': '2022-11-28',
      ...(init.headers || {})
    }
  });
  const text = await response.text();
  const body = text ? JSON.parse(text) : {};
  if (!response.ok) throw new Error(`github_${response.status}:${body.message || 'request_failed'}`);
  return body;
}

async function withAskPass(token, fn) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'inqsi-publisher-auth-'));
  const askPass = path.join(dir, 'askpass.sh');
  fs.writeFileSync(askPass, '#!/bin/sh\ncase "$1" in\n  *Username*) printf "%s\\n" "x-access-token" ;;\n  *) printf "%s\\n" "$INQSI_PUBLISH_TOKEN" ;;\nesac\n', { mode: 0o700 });
  const env = { ...process.env, GIT_ASKPASS: askPass, GIT_TERMINAL_PROMPT: '0', INQSI_PUBLISH_TOKEN: token };
  try { return await fn(env); }
  finally { fs.rmSync(dir, { recursive: true, force: true }); }
}

async function git(cwd, args, env = process.env) {
  const { stdout } = await exec('git', ['-c', 'core.hooksPath=/dev/null', ...args], { cwd, env, maxBuffer: 25 * 1024 * 1024 });
  return stdout.trim();
}

async function waitForChecks(config, sha) {
  const deadline = Date.now() + config.checkTimeoutMs;
  while (Date.now() < deadline) {
    const data = await github(config, `/commits/${sha}/check-runs?per_page=100`);
    const runs = data.check_runs || [];
    if (runs.some((run) => run.status === 'completed' && !['success', 'neutral', 'skipped'].includes(run.conclusion))) return { ok: false, runs };
    if (checksAllowMerge(runs)) return { ok: true, runs };
    await sleep(config.pollMs);
  }
  throw new Error('publish_checks_timeout');
}

async function processBundle(config, store, bundleDir) {
  const manifest = JSON.parse(fs.readFileSync(path.join(bundleDir, 'manifest.json'), 'utf8'));
  const patch = fs.readFileSync(path.join(bundleDir, 'patch.diff'), 'utf8');
  validateManifest(manifest, patch);
  const job = store.get(manifest.jobId);
  if (!job || job.startingRevision !== manifest.startingRevision) throw new Error('publish_job_mismatch');

  const checkout = fs.mkdtempSync(path.join(os.tmpdir(), 'inqsi-publisher-work-'));
  try {
    await git(checkout, ['init']);
    await git(checkout, ['remote', 'add', 'origin', `https://github.com/${config.repository}.git`]);
    await git(checkout, ['fetch', '--depth=1', 'origin', manifest.startingRevision]);
    await git(checkout, ['checkout', '-b', manifest.proposedBranch, manifest.startingRevision]);
    fs.writeFileSync(path.join(checkout, '.inqsi.patch'), patch, { mode: 0o600 });
    await git(checkout, ['apply', '--index', '--binary', '.inqsi.patch']);
    fs.rmSync(path.join(checkout, '.inqsi.patch'), { force: true });

    const applied = await collectChanges(checkout, manifest.startingRevision);
    const expected = [...manifest.changedFiles].sort();
    if (JSON.stringify(applied.changedFiles) !== JSON.stringify(expected)) throw new Error('publish_applied_file_set_mismatch');
    if (applied.changedFiles.some((file) => !within(file, manifest.authorizedScope))) throw new Error('publish_applied_scope_violation');

    await git(checkout, ['config', 'user.name', 'InQsi Engineering Publisher']);
    await git(checkout, ['config', 'user.email', 'inqsi-engineering@users.noreply.github.com']);
    await git(checkout, ['commit', '-m', `InQsi engineering job ${manifest.jobId}`]);
    const commit = await git(checkout, ['rev-parse', 'HEAD']);

    await withAskPass(config.token, (env) => git(checkout, ['push', '--set-upstream', 'origin', `${manifest.proposedBranch}:${manifest.proposedBranch}`], env));
    const title = String(job.instruction || 'InQsi engineering change').split('\n')[0].slice(0, 120) || `InQsi engineering job ${manifest.jobId}`;
    const pr = await github(config, '/pulls', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ title, head: manifest.proposedBranch, base: config.baseBranch, body: `Automated InQsi engineering job ${manifest.jobId}.\n\nAuthorized scope: ${manifest.authorizedScope.join(', ')}\n\nStarting revision: ${manifest.startingRevision}` })
    });

    job.commit = commit;
    job.pullRequest = pr.html_url;
    job.publishState = 'published';
    job.status = 'published';
    store.save(job);

    if (config.autoMerge) {
      const checks = await waitForChecks(config, commit);
      if (!checks.ok) {
        job.publishState = 'checks_failed';
        job.status = 'blocked';
        job.error = 'published_checks_failed';
        store.save(job);
        return;
      }
      const merge = await github(config, `/pulls/${pr.number}/merge`, {
        method: 'PUT',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ sha: commit, merge_method: 'merge', commit_title: title })
      });
      if (!merge.merged) throw new Error(`publish_merge_failed:${merge.message || 'not_merged'}`);
      job.mergeCommit = merge.sha;
      job.publishState = 'merged';
      job.status = 'merged';
      store.save(job);
    }

    fs.mkdirSync(config.processedDir, { recursive: true, mode: 0o700 });
    fs.renameSync(bundleDir, path.join(config.processedDir, manifest.jobId));
  } finally {
    fs.rmSync(checkout, { recursive: true, force: true });
  }
}

export async function runPublisherOnce(config = publisherConfig()) {
  const store = new JobStore(config.dataDir);
  fs.mkdirSync(config.outboxDir, { recursive: true, mode: 0o700 });
  const ids = fs.readdirSync(config.outboxDir).filter((id) => JOB_ID.test(id)).sort();
  for (const id of ids) {
    const bundleDir = path.join(config.outboxDir, id);
    try { await processBundle(config, store, bundleDir); }
    catch (error) {
      const job = store.get(id);
      if (job) {
        job.status = 'blocked';
        job.publishState = 'failed';
        job.error = sanitize(error?.message || error);
        store.save(job);
      }
    }
  }
}

export async function runPublisherLoop(config = publisherConfig()) {
  for (;;) {
    await runPublisherOnce(config);
    await sleep(config.pollMs);
  }
}

if (process.argv[1] === new URL(import.meta.url).pathname) runPublisherLoop().catch((error) => { console.error(sanitize(error?.message || error)); process.exit(1); });
