import { EventEmitter } from 'node:events';

const TRANSIENT_PERSISTENCE = new Set(['ESTALE', 'EWRITEUNKNOWN', 'EBUSY']);

export class DurableQueue extends EventEmitter {
  constructor(store, run, { maxConcurrent = 1 } = {}) {
    super();
    this.store = store;
    this.run = run;
    this.maxConcurrent = maxConcurrent;
    this.pending = [];
    this.active = new Map();
    this.draining = false;
    this.persistenceRetries = new Map();
  }

  enqueue(id) {
    if (this.active.has(id) || this.pending.includes(id)) return false;
    this.pending.push(id);
    queueMicrotask(() => this.drain());
    return true;
  }

  #schedulePersistenceRetry(id, attempt = 0) {
    if (this.persistenceRetries.has(id)) return;
    const delay = Math.min(30000, 50 * (2 ** Math.min(attempt, 9)));
    const timer = setTimeout(async () => {
      this.persistenceRetries.delete(id);
      try {
        if (this.active.has(id) || this.pending.includes(id)) return;
        const latest = this.store.get(id);
        if (!latest || ['completed', 'failed', 'cancelled', 'blocked'].includes(latest.status) ||
            ['merged', 'merge_conflict'].includes(latest.publicationState)) return;
        if (latest.cancelRequested && ['queued', 'running'].includes(latest.status)) {
          latest.status = 'cancelled';
          await this.store.saveAsync(latest);
          this.emit(id);
          return;
        }
        if (latest.status === 'running') {
          latest.status = 'queued';
          await this.store.saveAsync(latest);
        }
        if (latest.status === 'queued') this.enqueue(id);
      } catch (error) {
        if (TRANSIENT_PERSISTENCE.has(error?.code)) {
          this.emit('persistence_error', { id, code: error.code, retry: attempt + 1 });
          this.#schedulePersistenceRetry(id, attempt + 1);
          return;
        }
        this.emit('persistence_error', { id, code: error?.code || 'EIO', terminal: true });
      }
    }, delay);
    timer.unref?.();
    this.persistenceRetries.set(id, timer);
  }

  async drain() {
    if (this.draining) return;
    this.draining = true;
    try {
      while (this.pending.length && this.active.size < this.maxConcurrent) {
        const id = this.pending.shift();
        const job = this.store.getForExecution(id);
        if (!job || job.status !== 'queued') continue;

        const controller = new AbortController();
        this.active.set(id, controller);
        Promise.resolve(this.run(job, controller.signal))
          .catch((error) => {
            if (TRANSIENT_PERSISTENCE.has(error?.code)) {
              this.emit('persistence_error', { id, code: error.code, retry: 0 });
              this.#schedulePersistenceRetry(id);
              return;
            }
            const latest = this.store.get(id);
            if (latest && !['merged', 'merge_conflict'].includes(latest.publicationState) && !['completed', 'failed', 'cancelled'].includes(latest.status)) {
              latest.status = 'failed';
              latest.error = error?.message || String(error);
              this.store.save(latest);
            }
          })
          .finally(() => {
            this.active.delete(id);
            this.emit(id);
            queueMicrotask(() => this.drain());
          });
      }
    } finally {
      this.draining = false;
    }
  }

  cancel(id) {
    const job = this.store.get(id);
    if (!job) return false;
    job.cancelRequested = true;
    if (job.status === 'queued') {
      job.status = 'cancelled';
      this.pending = this.pending.filter((pendingId) => pendingId !== id);
    }
    this.store.save(job);
    this.active.get(id)?.abort();
    this.emit(id);
    return true;
  }
}
