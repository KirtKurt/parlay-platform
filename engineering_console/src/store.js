import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';

export class JobStore {
  constructor(directory) { this.directory = directory; fs.mkdirSync(directory, { recursive: true, mode: 0o700 }); }
  file(id) { return path.join(this.directory, `${id}.json`); }
  create(input, owner, revision) {
    const now = new Date().toISOString();
    const job = { id: crypto.randomUUID(), owner, instruction: input.instruction, repository: 'KirtKurt/parlay-platform', authorizedScope: input.authorizedScope, startingRevision: revision, status: 'queued', createdAt: now, updatedAt: now, cancelRequested: false, logs: [], changedFiles: [], testResults: [], threadId: null, branch: null, commit: null, pullRequest: null, error: null };
    this.save(job); return job;
  }
  save(job) { job.updatedAt = new Date().toISOString(); const target = this.file(job.id); const temp = `${target}.${process.pid}.tmp`; fs.writeFileSync(temp, `${JSON.stringify(job, null, 2)}\n`, { mode: 0o600 }); fs.renameSync(temp, target); }
  get(id) { try { return JSON.parse(fs.readFileSync(this.file(id), 'utf8')); } catch (error) { if (error.code === 'ENOENT') return null; throw error; } }
  list(owner) { return fs.readdirSync(this.directory).filter((x) => x.endsWith('.json')).map((x) => this.get(x.slice(0, -5))).filter((job) => job?.owner === owner).sort((a,b) => b.createdAt.localeCompare(a.createdAt)); }
  owned(id, owner) { const job = this.get(id); return job?.owner === owner ? job : null; }
}
