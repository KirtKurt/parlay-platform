import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { sanitizeValue } from './sanitize.js';

const JOB_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const MAX_JOB_RECORD_BYTES = 16 * 1024 * 1024;
const WRITER = fileURLToPath(new URL('../scripts/store-write.mjs', import.meta.url));

export class JobStore {
  constructor(directory) {
    this.directory = path.resolve(directory);
    this.snapshots = new WeakMap();
    this.runtimeInstructions = new Map();
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
    this.runtimeInstructions.set(job.id, job.instruction);
    this.save(job);
    return job;
  }

  #prepare(job) {
    const persistedJob = sanitizeValue(job);
    const jobBytes = Buffer.byteLength(JSON.stringify(persistedJob));
    if (jobBytes > MAX_JOB_RECORD_BYTES) {
      throw Object.assign(new Error('job_store_record_too_large'), { code: 'EFBIG' });
    }
    if (typeof job.instruction === 'string') this.runtimeInstructions.set(job.id, job.instruction);
    return {
      target: this.file(job.id),
      payload: JSON.stringify({ base: this.snapshots.get(job) ?? null, job: persistedJob })
    };
  }

  #openLock(target) {
    const fd = fs.openSync(`${target}.lock`, fs.constants.O_CREAT | fs.constants.O_RDWR | fs.constants.O_NOFOLLOW, 0o600);
    const stat = fs.fstatSync(fd);
    if (!stat.isFile() || stat.nlink !== 1 || stat.uid !== process.getuid() || (stat.mode & 0o022)) {
      fs.closeSync(fd);
      throw new Error('job_store_lock_not_trusted');
    }
    return fd;
  }

  #applyCommitted(job, target) {
    const persisted = JSON.parse(fs.readFileSync(target, 'utf8'));
    const runtimeInstruction = this.runtimeInstructions.get(job.id);
    for (const key of Object.keys(job)) if (!(key in persisted)) delete job[key];
    Object.assign(job, persisted);
    if (runtimeInstruction !== undefined) job.instruction = runtimeInstruction;
    this.snapshots.set(job, structuredClone(persisted));
  }

  #writeError(status, error) {
    if (status === 73) return Object.assign(new Error('job_store_write_conflict'), { code: 'ESTALE' });
    if (error) return Object.assign(new Error('job_store_write_failed'), { code: 'EIO', cause: error });
    return Object.assign(new Error('job_store_write_failed'), { code: status === 75 ? 'EBUSY' : 'EIO' });
  }

  save(job) {
    const { target, payload } = this.#prepare(job);
    const fd = this.#openLock(target);
    let result;
    try {
      result = spawnSync('flock', ['--exclusive', '--timeout', '5', '--conflict-exit-code', '75', '--no-fork', '/proc/self/fd/3', process.execPath, WRITER, target], {
        input: payload,
        encoding: 'utf8',
        stdio: ['pipe', 'pipe', 'pipe', fd],
        maxBuffer: 1024 * 1024
      });
    } finally { fs.closeSync(fd); }
    if (result.error || result.status !== 0) throw this.#writeError(result.status, result.error);
    this.#applyCommitted(job, target);
  }

  async saveAsync(job) {
    const { target, payload } = this.#prepare(job);
    const fd = this.#openLock(target);
    let child;
    try {
      child = spawn('flock', ['--exclusive', '--timeout', '5', '--conflict-exit-code', '75', '--no-fork', '/proc/self/fd/3', process.execPath, WRITER, target], {
        stdio: ['pipe', 'ignore', 'ignore', fd]
      });
    } catch (error) {
      fs.closeSync(fd);
      throw this.#writeError(null, error);
    }
    fs.closeSync(fd);
    child.stdin.end(payload);
    const status = await new Promise((resolve, reject) => {
      child.once('error', reject);
      child.once('close', resolve);
    }).catch((error) => { throw this.#writeError(null, error); });
    if (status !== 0) throw this.#writeError(status);
    this.#applyCommitted(job, target);
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

  getForExecution(id) {
    const job = this.get(id);
    if (job && this.runtimeInstructions.has(id)) job.instruction = this.runtimeInstructions.get(id);
    return job;
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
