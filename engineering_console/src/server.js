import http from 'node:http';
import fs from 'node:fs';
import { loadConfig } from './config.js';
import { createAuthorizer } from './auth.js';
import { JobStore } from './store.js';
import { resolveRevision } from './git.js';
import { DurableQueue } from './queue.js';
import { createRunner } from './worker-runtime.js';
import { publicJob } from './sanitize.js';

function validScope(scope, allowedScopes) {
  if (typeof scope !== 'string') return false;
  const value = scope.trim().replaceAll('\\', '/').replace(/^\.\//, '').replace(/\/$/, '');
  if (!value || value === '.' || value.startsWith('/') || value.split('/').some((part) => !part || part === '..' || part === '.git')) return false;
  return allowedScopes.some((root) => value === root || value.startsWith(`${root}/`));
}

export function createServer({ config = loadConfig(), authorizer, store, queue } = {}) {
  store ||= new JobStore(config.dataDir);
  authorizer ||= createAuthorizer(config);
  queue ||= new DurableQueue(store, createRunner(config, store), { maxConcurrent: config.maxConcurrentJobs });

  for (const name of fs.readdirSync(config.dataDir).filter((x) => x.endsWith('.json'))) {
    const recovered = store.get(name.slice(0, -5));
    if (!recovered) continue;
    if (recovered.cancelRequested && ['queued', 'running'].includes(recovered.status)) {
      recovered.status = 'cancelled';
      store.save(recovered);
    } else if (['queued', 'running'].includes(recovered.status)) {
      recovered.status = 'queued';
      store.save(recovered);
      queue.enqueue(recovered.id);
    }
  }

  return http.createServer(async (request, response) => {
    const send = (status, value) => { response.writeHead(status, { 'content-type': 'application/json', 'cache-control': 'no-store', 'x-content-type-options': 'nosniff' }); response.end(JSON.stringify(value)); };
    try {
      if (!request.url.startsWith('/v1/engineering')) return send(404, { error: 'not_found' });
      const actor = await authorizer(request);
      const url = new URL(request.url, 'http://localhost'); const parts = url.pathname.split('/').filter(Boolean); const id = parts[2]; const action = parts[3];
      let body = {}; if (['POST','PUT'].includes(request.method)) { let raw=''; for await (const chunk of request) { raw += chunk; if (raw.length > config.maxInstructionBytes + 10000) throw Object.assign(new Error('request_too_large'), { status: 413 }); } try { body = raw ? JSON.parse(raw) : {}; } catch { throw Object.assign(new Error('invalid_json'), { status: 400 }); } }
      if (request.method === 'GET' && !id) return send(200, { jobs: store.list(actor.id).map(publicJob) });
      if (request.method === 'POST' && !id) {
        if (!String(body.instruction || '').trim()) return send(400, { error: 'instruction_required' });
        if (!Array.isArray(body.authorizedScope) || !body.authorizedScope.length || body.authorizedScope.some((scope) => !validScope(scope, config.allowedScopes))) return send(400, { error: 'valid_authorized_scope_required' });
        const revision = await resolveRevision(config.repository, body.startingRevision || 'HEAD'); const job = store.create(body, actor.id, revision); queue.enqueue(job.id); return send(202, { job: publicJob(job) });
      }
      const job = id && store.owned(id, actor.id); if (!job) return send(404, { error: 'job_not_found' });
      if (request.method === 'GET' && !action) return send(200, { job: publicJob(job) });
      if (request.method === 'GET' && action === 'events') { response.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-store', connection: 'keep-alive' }); const push=()=>response.write(`data: ${JSON.stringify(publicJob(store.owned(id, actor.id)))}\n\n`); push(); const interval=setInterval(push, 2000); request.on('close',()=>clearInterval(interval)); return; }
      if (request.method === 'POST' && action === 'cancel') { queue.cancel(id); return send(202, { job: publicJob(store.owned(id, actor.id)) }); }
      if (request.method === 'POST' && action === 'continue') { if (!['completed','failed','blocked','awaiting_approval'].includes(job.status)) return send(409, { error: 'job_not_continuable' }); if (!String(body.instruction || '').trim()) return send(400, { error: 'instruction_required' }); job.instruction = String(body.instruction); job.status = 'queued'; job.cancelRequested = false; job.error = null; store.save(job); queue.enqueue(id); return send(202, { job: publicJob(job) }); }
      return send(404, { error: 'not_found' });
    } catch (error) { send(error.status || 500, { error: error.status ? error.message : 'internal_error' }); }
  });
}

if (process.argv[1] === new URL(import.meta.url).pathname) { const config = loadConfig(); createServer({ config }).listen(config.port, '127.0.0.1', () => console.log(`InQsi engineering service listening on ${config.port}`)); }
