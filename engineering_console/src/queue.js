import { EventEmitter } from 'node:events';
export class DurableQueue extends EventEmitter {
  constructor(store, run) { super(); this.store = store; this.run = run; this.pending = []; this.active = new Map(); }
  enqueue(id) { if (!this.pending.includes(id)) this.pending.push(id); queueMicrotask(() => this.drain()); }
  async drain() { if (!this.pending.length) return; const id = this.pending.shift(); const job = this.store.get(id); if (!job || job.status !== 'queued') return this.drain(); const controller = new AbortController(); this.active.set(id, controller); try { await this.run(job, controller.signal); } finally { this.active.delete(id); this.emit(id); this.drain(); } }
  cancel(id) { const job = this.store.get(id); if (!job) return false; job.cancelRequested = true; if (job.status === 'queued') job.status = 'cancelled'; this.store.save(job); this.active.get(id)?.abort(); this.emit(id); return true; }
}
