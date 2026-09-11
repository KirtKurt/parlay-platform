import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { sanitizeValue } from './sanitize.js';

const JOB_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export class JobStore {
  constructor(directory) {
    this.directory = path.resolve(directory);
    this.snapshots = new WeakMap();
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
    const target = this.file(job.id);
    // Hold one shared kernel lock across read/compare/write. Never unlink it:
    // replacing the inode would let concurrent containers hold different locks.
    const fd = fs.openSync(`${target}.lock`, fs.constants.O_CREAT | fs.constants.O_RDWR | fs.constants.O_NOFOLLOW, 0o600);
    let result;
    try {
      const stat = fs.fstatSync(fd);
      if (!stat.isFile() || stat.nlink !== 1 || stat.uid !== process.getuid() || (stat.mode & 0o022)) throw new Error('job_store_lock_not_trusted');
      result = spawnSync('flock', ['--exclusive', '--timeout', '5', '--conflict-exit-code', '75', '--no-fork', '/proc/self/fd/3', process.execPath, fileURLToPath(new URL('../scripts/store-write.mjs', import.meta.url)), target], {
        input: JSON.stringify({ base: this.snapshots.get(job) ?? null, job: sanitizeValue(job) }),
        encoding: 'utf8', stdio: ['pipe', 'pipe', 'pipe', fd], maxBuffer: 32 * 1024 * 1024
      });
    } finally { fs.closeSync(fd); }
    if (result.error || result.status !== 0) {
      const conflict = result.status === 73;
      throw Object.assign(new Error(conflict ? 'job_store_write_conflict' : 'job_store_write_failed'), { code: conflict ? 'ESTALE' : 'EIO' });
    }
    const persisted = JSON.parse(result.stdout);
    for (const key of Object.keys(job)) if (!(key in persisted)) delete job[key];
    Object.assign(job, persisted);
    this.snapshots.set(job, structuredClone(persisted));
  }

  get(id) {
    let target;
    try { target = this.file(id); }
    catch (error) { if (error.code === 'EINVAL') return null; throw error; }
    try {
      const job = JSON.parse(fs.readFileSync(target, 'utf8'));
      this.snapshots.set(job, structuredClone(job));
      return job;
    }
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
