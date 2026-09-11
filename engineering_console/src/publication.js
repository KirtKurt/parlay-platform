import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

export const JOB_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const SHA = /^[0-9a-f]{40}$/i;
const PASSING = new Set(['success', 'neutral', 'skipped']);
const SECRET_PATTERNS = [
  /\bsk-(?:proj-)?[A-Za-z0-9_-]{12,}\b/,
  /\bgh[pousr]_[A-Za-z0-9_]{20,}\b/,
  /\b(?:AKIA|ASIA)[0-9A-Z]{16}\b/,
  /authorization\s*:\s*bearer\s+[^\s,;]+/i,
  /\b(?:api[_-]?key|token|secret|password|client[_-]?secret|access[_-]?key)\s*[:=]\s*[^\s,;]+/i
];

export function sha256(value) {
  return crypto.createHash('sha256').update(String(value ?? '')).digest('hex');
}

export function patchContainsCredential(value) {
  return SECRET_PATTERNS.some((pattern) => pattern.test(String(value || '')));
}

export function normalizeRepoPath(value) {
  if (typeof value !== 'string' || value.includes('\\')) return null;
  const raw = value.trim().replace(/^\.\//, '').replace(/\/$/, '');
  if (!raw || raw === '.' || raw.startsWith('/')) return null;
  const parts = raw.split('/');
  if (parts.some((part) => !part || part === '.' || part === '..' || part === '.git')) return null;
  return raw;
}

export function withinAuthorizedScope(file, scopes) {
  const value = normalizeRepoPath(file);
  if (!value || !Array.isArray(scopes) || !scopes.length) return false;
  return scopes.some((scope) => {
    const root = normalizeRepoPath(scope);
    return Boolean(root) && (value === root || value.startsWith(`${root}/`));
  });
}

export function evaluateRequiredChecks(checkRuns, requiredChecks) {
  if (!Array.isArray(requiredChecks) || !requiredChecks.length) return { state: 'failed', reason: 'required_checks_missing' };
  if (!Array.isArray(checkRuns)) return { state: 'pending', reason: 'check_runs_unavailable' };

  const newest = new Map();
  for (const run of checkRuns) {
    if (!requiredChecks.includes(run?.name)) continue;
    const previous = newest.get(run.name);
    if (!previous || Number(run.id || 0) > Number(previous.id || 0)) newest.set(run.name, run);
  }

  for (const name of requiredChecks) {
    const run = newest.get(name);
    if (!run) return { state: 'pending', reason: `required_check_not_observed:${name}` };
    if (run.status !== 'completed') return { state: 'pending', reason: `required_check_incomplete:${name}` };
    if (!PASSING.has(run.conclusion)) return { state: 'failed', reason: `required_check_failed:${name}` };
  }
  return { state: 'passed', reason: null };
}

export function validatePublicationManifest(manifest, patch) {
  if (manifest?.version !== 1 || !JOB_ID.test(manifest.jobId) || !SHA.test(manifest.startingRevision)) throw new Error('invalid_publication_manifest');
  if (manifest.repository !== 'KirtKurt/parlay-platform') throw new Error('unexpected_publication_repository');
  if (manifest.branch !== `inqsi/publish-${manifest.jobId}`) throw new Error('invalid_publication_branch');
  if (!Array.isArray(manifest.authorizedScope) || !manifest.authorizedScope.length || manifest.authorizedScope.some((scope) => !normalizeRepoPath(scope))) throw new Error('invalid_publication_scope');
  if (!Array.isArray(manifest.changedFiles) || !manifest.changedFiles.length) throw new Error('publication_has_no_changes');
  if (manifest.changedFiles.some((file) => !withinAuthorizedScope(file, manifest.authorizedScope))) throw new Error('publication_scope_violation');
  if (!Array.isArray(manifest.requiredChecks) || !manifest.requiredChecks.length || manifest.requiredChecks.some((name) => typeof name !== 'string' || !name.trim())) throw new Error('invalid_required_checks');
  if (sha256(patch) !== manifest.patchSha256) throw new Error('publication_patch_hash_mismatch');
  if (patchContainsCredential(patch)) throw new Error('publication_patch_secret_detected');
  return true;
}

export function publicationDirectories(dataDir) {
  const root = path.resolve(dataDir);
  return {
    outbox: path.join(root, 'publication-outbox'),
    receipts: path.join(root, 'publication-receipts'),
    processed: path.join(root, 'publication-processed')
  };
}

export function writePublicationRequest(config, job, patch) {
  if (!JOB_ID.test(job?.id || '')) throw new Error('invalid_job_id');
  if (!Array.isArray(job.changedFiles) || !job.changedFiles.length) return null;
  if (patchContainsCredential(patch)) throw new Error('publication_patch_secret_detected');

  const dirs = publicationDirectories(config.dataDir);
  fs.mkdirSync(dirs.outbox, { recursive: true, mode: 0o700 });
  const finalDir = path.join(dirs.outbox, job.id);
  const manifest = {
    version: 1,
    jobId: job.id,
    repository: job.repository,
    startingRevision: job.startingRevision,
    branch: `inqsi/publish-${job.id}`,
    authorizedScope: [...job.authorizedScope],
    changedFiles: [...job.changedFiles].sort(),
    requiredChecks: [...config.requiredChecks],
    patchSha256: sha256(patch),
    createdAt: new Date().toISOString()
  };

  if (fs.existsSync(finalDir)) {
    const existing = JSON.parse(fs.readFileSync(path.join(finalDir, 'manifest.json'), 'utf8'));
    if (existing.patchSha256 !== manifest.patchSha256 || existing.startingRevision !== manifest.startingRevision) throw new Error('publication_request_collision');
    return existing;
  }

  const tempDir = path.join(dirs.outbox, `.${job.id}.${process.pid}.tmp`);
  fs.mkdirSync(tempDir, { mode: 0o700 });
  try {
    fs.writeFileSync(path.join(tempDir, 'patch.diff'), patch, { mode: 0o600 });
    fs.writeFileSync(path.join(tempDir, 'manifest.json'), `${JSON.stringify(manifest, null, 2)}\n`, { mode: 0o600 });
    fs.renameSync(tempDir, finalDir);
  } catch (error) {
    fs.rmSync(tempDir, { recursive: true, force: true });
    throw error;
  }
  return manifest;
}

export function readPublicationReceipt(dataDir, jobId) {
  if (!JOB_ID.test(jobId || '')) return null;
  const { receipts } = publicationDirectories(dataDir);
  try { return JSON.parse(fs.readFileSync(path.join(receipts, `${jobId}.json`), 'utf8')); }
  catch (error) { if (error.code === 'ENOENT') return null; throw error; }
}

export function writePublicationReceipt(dataDir, receipt) {
  if (!JOB_ID.test(receipt?.jobId || '')) throw new Error('invalid_job_id');
  const { receipts } = publicationDirectories(dataDir);
  fs.mkdirSync(receipts, { recursive: true, mode: 0o700 });
  const target = path.join(receipts, `${receipt.jobId}.json`);
  const temp = `${target}.${process.pid}.tmp`;
  fs.writeFileSync(temp, `${JSON.stringify({ ...receipt, updatedAt: new Date().toISOString() }, null, 2)}\n`, { mode: 0o600 });
  fs.renameSync(temp, target);
}
