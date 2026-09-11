import { EventEmitter } from 'node:events';

export class DurableQueue extends EventEmitter {
  constructor(store, run, { maxConcurrent = 1, recoveryDelayMs = 250 } = {}) {
    super();
    this.store = store;
    this.run = run;
    this.maxConcurrent = maxConcurrent;
    this.pending = [];
    this.active = new Map();
    this.draining = false;
    this.recoveryDelayMs = recoveryDelayMs;
    this.recoveries = new Map();
    this.closed = false;
  }

  close() {
    this.closed = true;
    for (const timer of this.recoveries.values()) clearTimeout(timer);
    this.recoveries.clear();
  }

  reconcileLater(id, attempt = 0) {
    if (this.closed || this.recoveries.has(id)) return;
    const timer = setTimeout(async () => {
      this.recoveries.delete(id);
      if (this.closed) return;
      if (this.active.has(id)) { this.reconcileLater(id, attempt + 1); return; }
      try {
        const latest = this.store.getForExecution(id);
        if (!latest || !['queued', 'running'].includes(latest.status) || (latest.publicationState && latest.publicationState !== 'no_changes')) return;
        if (latest.cancelRequested && !latest.execution) latest.status = 'cancelled';
        else if (latest.instructionRecoverable === false && !latest.execution && !this.store.hasRuntimeInstruction(id)) {
          latest.status = 'blocked'; latest.error = 'runtime_instruction_unavailable_after_restart';
        } else latest.status = 'queued';
        await this.store.saveAsync(latest);
        if (latest.status === 'queued') this.enqueue(id);
        else this.emit(id);
      } catch (error) {
        this.emit('persistence_error', { id, code: error.code || 'EREAD' });
        this.reconcileLater(id, attempt + 1);
      }
    }, Math.min(this.recoveryDelayMs * 2 ** Math.min(attempt, 8), 30000));
    timer.unref();
    this.recoveries.set(id, timer);
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
        const job = this.store.getForExecution(id);
        if (!job || job.status !== 'queued') continue;

        const controller = new AbortController();
        this.active.set(id, controller);
        Promise.resolve(this.run(job, controller.signal))
          .catch(async (error) => {
            if (['ESTALE', 'EWRITEUNKNOWN', 'EBUSY'].includes(error?.code)) {
              this.emit('persistence_error', { id, code: error.code });
              this.reconcileLater(id);
              return;
            }
            const latest = this.store.get(id);
            if (latest && !['merged', 'merge_conflict'].includes(latest.publicationState) && !['completed', 'failed', 'cancelled'].includes(latest.status)) {
              latest.status = 'failed';
              latest.error = error?.message || String(error);
              await this.store.saveAsync(latest);
            }
          })
          .catch(error => { this.emit('persistence_error', { id, code: error.code || 'EWRITEUNKNOWN' }); this.reconcileLater(id); })
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
    if (job.status === 'queued' && !job.execution) {
      job.status = 'cancelled';
      this.pending = this.pending.filter((pendingId) => pendingId !== id);
    }
    this.store.save(job);
    this.active.get(id)?.abort();
    this.emit(id);
    return true;
  }
}
