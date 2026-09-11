import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { JOB_ID, publicationDirectories } from './publication.js';

function decisionPath(dataDir, jobId) {
  if (!JOB_ID.test(jobId || '')) throw new Error('invalid_job_id');
  const root = path.join(path.dirname(publicationDirectories(dataDir).outbox), 'publication-decisions');
  fs.mkdirSync(root, { recursive: true, mode: 0o700 });
  return path.join(root, jobId);
}

function syncDirectory(directory) {
  const fd = fs.openSync(directory, fs.constants.O_RDONLY | fs.constants.O_DIRECTORY | fs.constants.O_NOFOLLOW);
  try { fs.fsyncSync(fd); }
  finally { fs.closeSync(fd); }
}

function readDecision(target) {
  let fd;
  try {
    fd = fs.openSync(target, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
    const stat = fs.fstatSync(fd);
    if (!stat.isFile() || stat.size > 64) throw new Error('invalid_publication_decision');
    const value = fs.readFileSync(fd, 'utf8').trim();
    if (value !== 'cancelled' && value !== 'merge') throw new Error('invalid_publication_decision');
    return value;
  } catch (error) {
    if (error.code === 'ENOENT') return null;
    throw error;
  } finally {
    if (fd !== undefined) fs.closeSync(fd);
  }
}

function decide(dataDir, jobId, value) {
  const target = decisionPath(dataDir, jobId);
  const directory = path.dirname(target);
  const staging = path.join(directory, `.${path.basename(target)}.${crypto.randomUUID()}.tmp`);
  let fd;
  let owned = false;
  try {
    fd = fs.openSync(staging,
      fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL | fs.constants.O_NOFOLLOW, 0o600);
    owned = true;
    fs.writeFileSync(fd, `${value}\n`, 'utf8');
    fs.fsyncSync(fd);
    fs.closeSync(fd);
    fd = undefined;
    try {
      fs.linkSync(staging, target);
      syncDirectory(directory);
      return value;
    } catch (error) {
      if (error.code !== 'EEXIST') throw error;
      syncDirectory(directory);
      return readDecision(target);
    }
  } finally {
    try { if (fd !== undefined) fs.closeSync(fd); }
    finally {
      if (owned) {
        try { fs.unlinkSync(staging); }
        catch (error) { if (error.code !== 'ENOENT') throw error; }
      }
    }
  }
}

/** First durable decision wins. A cancellation accepted before merge commitment
 * makes publication permanently non-mergeable; once merge commitment wins,
 * cancellation returns false so the API can report that it is too late. */
export function cancelPublication(dataDir, jobId) {
  return decide(dataDir, jobId, 'cancelled') === 'cancelled';
}

/** Returns true for a new or recovered merge commitment. Throws if an accepted
 * cancellation won the decision race. The persistent `merge` state allows a
 * crashed publisher to reconcile/retry without reopening the cancellation race. */
export function beginPublicationMerge(dataDir, jobId) {
  const decision = decide(dataDir, jobId, 'merge');
  if (decision === 'cancelled') throw new Error('publication_cancelled');
  if (decision !== 'merge') throw new Error('invalid_publication_decision');
  return true;
}

export function publicationDecision(dataDir, jobId) {
  return readDecision(decisionPath(dataDir, jobId));
}
