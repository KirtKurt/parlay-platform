import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { sanitizeValue } from './sanitize.js';

const JOB_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export class JobStore {
  constructor(directory) {
    this.directory = path.resolve(directory);
    fs.mkdirSync(this.directory, { recursive: true, mode: 0o700 });
  }

  file(id) {
    if (!JOB_ID.test(String(id || ''))) throw Object.assign(new Error('invalid_job_id'), { code: 'EINVAL' });
    const target = path.resolve(this.directory, `${id}.json`);
    if (path.dirname(target) !== this.directory) throw Object.assign(new Error('invalid_job_id'), { code: 'EINVAL' });
    return target;
  }

  create(input, owner, revision) {
    const now = new Date().toISOString();
    const job = {
      id: crypto.randomUUID(),
      owner,
      instruction: input.instruction,
      repository: 'KirtKurt/parlay-platform',
      authorizedScope: input.authorizedScope,
      startingRevision: revision,
      status: 'queued',
      createdAt: now,
      updatedAt: now,
      cancelRequested: false,
      logs: [],
      changedFiles: [],
      testResults: [],
      threadId: null,
      branch: null,
      commit: null,
      pullRequest: null,
      error: null
    };
    this.save(job);
    return job;
  }

  save(job) {
    job.updatedAt = new Date().toISOString();
    const target = this.file(job.id);
    const temp = `${target}.${process.pid}.tmp`;
    const persisted = sanitizeValue(job);
    fs.writeFileSync(temp, `${JSON.stringify(persisted, null, 2)}\n`, { mode: 0o600 });
    fs.renameSync(temp, target);
    Object.assign(job, persisted);
  }

  get(id) {
    let target;
    try { target = this.file(id); }
    catch (error) { if (error.code === 'EINVAL') return null; throw error; }
    try { return JSON.parse(fs.readFileSync(target, 'utf8')); }
    catch (error) { if (error.code === 'ENOENT') return null; throw error; }
  }

  list(owner) {
    return fs.readdirSync(this.directory)
      .filter((name) => name.endsWith('.json'))
      .map((name) => name.slice(0, -5))
      .filter((id) => JOB_ID.test(id))
      .map((id) => this.get(id))
      .filter((job) => job?.owner === owner)
      .sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  }

  owned(id, owner) {
    const job = this.get(id);
    return job?.owner === owner ? job : null;
  }
}
