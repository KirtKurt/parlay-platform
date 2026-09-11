import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

const SECRET_PATTERNS = [
  /\bsk-(?:proj-)?[A-Za-z0-9_-]{12,}\b/,
  /\bgh[pousr]_[A-Za-z0-9_]{20,}\b/,
  /\b(?:AKIA|ASIA)[0-9A-Z]{16}\b/,
  /authorization\s*:\s*bearer\s+[^\s,;]+/i,
  /\b(?:api[_-]?key|token|secret|password|client[_-]?secret|access[_-]?key)\s*[:=]\s*[^\s,;]+/i
];

function sha256(value) {
  return crypto.createHash('sha256').update(value).digest('hex');
}

export function patchContainsCredential(value) {
  return SECRET_PATTERNS.some((pattern) => pattern.test(String(value || '')));
}

export function writePublishBundle(config, job, rawDiff) {
  if (patchContainsCredential(rawDiff)) throw new Error('publish_bundle_secret_detected');
  if (!Array.isArray(job.changedFiles) || !job.changedFiles.length) return null;

  fs.mkdirSync(config.outboxDir, { recursive: true, mode: 0o700 });
  const finalDir = path.join(config.outboxDir, job.id);
  if (fs.existsSync(finalDir)) return { id: job.id, patchSha256: sha256(rawDiff) };

  const tempDir = path.join(config.outboxDir, `.${job.id}.${process.pid}.tmp`);
  fs.mkdirSync(tempDir, { mode: 0o700 });
  const patchSha256 = sha256(rawDiff);
  const manifest = {
    version: 1,
    jobId: job.id,
    repository: job.repository,
    startingRevision: job.startingRevision,
    proposedBranch: job.branch,
    authorizedScope: job.authorizedScope,
    changedFiles: job.changedFiles,
    patchSha256,
    createdAt: new Date().toISOString()
  };

  fs.writeFileSync(path.join(tempDir, 'patch.diff'), rawDiff, { mode: 0o600 });
  fs.writeFileSync(path.join(tempDir, 'manifest.json'), `${JSON.stringify(manifest, null, 2)}\n`, { mode: 0o600 });
  fs.renameSync(tempDir, finalDir);
  return { id: job.id, patchSha256 };
}
