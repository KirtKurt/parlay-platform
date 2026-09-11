import fs from 'node:fs';
import path from 'node:path';
import { JOB_ID, publicationDirectories } from './publication.js';

function decisionPath(dataDir, jobId) {
  if (!JOB_ID.test(jobId || '')) throw new Error('invalid_job_id');
  const root = path.join(path.dirname(publicationDirectories(dataDir).outbox), 'publication-decisions');
  fs.mkdirSync(root, { recursive: true, mode: 0o700 });
  return path.join(root, jobId);
}

function parseDecision(value) {
  const text = String(value || '').trim();
  if (text === 'cancelled' || text === 'merge') return { value: text, decidedAt: null };
  let parsed;
  try { parsed = JSON.parse(text); }
  catch { throw new Error('invalid_publication_decision'); }
  if (parsed?.version !== 1 || (parsed.value !== 'cancelled' && parsed.value !== 'merge')) throw new Error('invalid_publication_decision');
  if (typeof parsed.decidedAt !== 'string' || !Number.isFinite(Date.parse(parsed.decidedAt))) throw new Error('invalid_publication_decision');
  return { value: parsed.value, decidedAt: parsed.decidedAt };
}

function readDecisionRecord(target) {
  try { return parseDecision(fs.readFileSync(target, 'utf8')); }
  catch (error) {
    if (error.code === 'ENOENT') return null;
    throw error;
  }
}

function decide(dataDir, jobId, value) {
  const target = decisionPath(dataDir, jobId);
  const record = { version: 1, value, decidedAt: new Date().toISOString() };
  try {
    fs.writeFileSync(target, `${JSON.stringify(record)}\n`, { mode: 0o600, flag: 'wx' });
    return record;
  } catch (error) {
    if (error.code !== 'EEXIST') throw error;
    return readDecisionRecord(target);
  }
}

/** First durable decision wins. A cancellation accepted before merge commitment
 * makes publication permanently non-mergeable; once merge commitment wins,
 * cancellation returns false so the API can report that it is too late. */
export function cancelPublication(dataDir, jobId) {
  return decide(dataDir, jobId, 'cancelled').value === 'cancelled';
}

/** Returns true for a new or recovered merge commitment. Throws if an accepted
 * cancellation won the decision race. The persistent `merge` state allows a
 * crashed publisher to reconcile/retry without reopening the cancellation race. */
export function beginPublicationMerge(dataDir, jobId) {
  const decision = decide(dataDir, jobId, 'merge');
  if (decision.value === 'cancelled') throw new Error('publication_cancelled');
  if (decision.value !== 'merge') throw new Error('invalid_publication_decision');
  return true;
}

export function publicationDecision(dataDir, jobId) {
  return readDecisionRecord(decisionPath(dataDir, jobId))?.value || null;
}

export function publicationDecisionRecord(dataDir, jobId) {
  return readDecisionRecord(decisionPath(dataDir, jobId));
}
