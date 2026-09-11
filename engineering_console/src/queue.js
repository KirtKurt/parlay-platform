import { EventEmitter } from 'node:events';

export class DurableQueue extends EventEmitter {
  constructor(store, run, { maxConcurrent = 1 } = {}) {
    super();
    this.store = store;
    this.run = run;
    this.maxConcurrent = maxConcurrent;
    this.pending = [];
    this.active = new Map();
    this.draining = false;
  }

  enqueue(id) {
    if (this.active.has(id) || this.pending.includes(id)) return false;
    this.pending.push(id);
    queueMicrotask(() => this.drain());
    return true;
  }

  async drain() {
    if (this.draining) return;
    this.draining = true;
    try {
      while (this.pending.length && this.active.size < this.maxConcurrent) {
        const id = this.pending.shift();
        const job = this.store.get(id);
        if (!job || job.status !== 'queued') continue;

        const controller = new AbortController();
        this.active.set(id, controller);
        Promise.resolve(this.run(job, controller.signal))
          .catch((error) => {
            if (error?.code === 'ESTALE') return;
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
