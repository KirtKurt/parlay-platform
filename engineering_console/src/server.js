import http from 'node:http';
import fs from 'node:fs';
import { loadConfig } from './config.js';
import { createAuthorizer } from './auth.js';
import { JobStore } from './store.js';
import { refreshRepository, resolveRevision } from './git.js';
import { DurableQueue } from './queue.js';
import { createRunner } from './worker-runtime.js';
import { publicJob } from './sanitize.js';
import { normalizeRepoPath } from './publication.js';
import { isPublishableScope } from './publication-policy.js';
import { assertMainAncestor } from './publication-git-guard.js';
import { cancelPublication } from './publication-decision.js';
import { markPublicationCancellationPending } from './publication-cancellation.js';
import { stopExecution } from './ecs-job.js';
import { createBrowserAuth } from './browser-auth.js';

function validScope(scope, allowedScopes) {
  const value = normalizeRepoPath(scope);
  if (!value || !isPublishableScope(scope)) return false;
  return allowedScopes.some((root) => value === root || value.startsWith(`${root}/`));
}

function validJobScopes(job, allowedScopes) {
  return Array.isArray(job?.authorizedScope) && job.authorizedScope.length > 0 && job.authorizedScope.every((scope) => validScope(scope, allowedScopes));
}

export function createServer({ config = loadConfig(), authorizer, store, queue } = {}) {
  store ||= new JobStore(config.dataDir);
  authorizer ||= createAuthorizer(config);
  const browserAuth = config.browserAuthEnabled ? createBrowserAuth(config, authorizer) : null;
  queue ||= new DurableQueue(store, createRunner(config, store), { maxConcurrent: config.maxConcurrentJobs });

  for (const name of fs.readdirSync(config.dataDir).filter((x) => x.endsWith('.json'))) {
    const recovered = store.get(name.slice(0, -5));
    if (!recovered) continue;
    if (recovered.cancelRequested && !recovered.execution && ['queued', 'running'].includes(recovered.status)) {
      recovered.status = 'cancelled';
      store.save(recovered);
    } else if (['queued', 'running'].includes(recovered.status) || (recovered.cancelRequested && recovered.execution && !recovered.execution.stoppedAt)) {
      if (!validJobScopes(recovered, config.allowedScopes)) {
        recovered.status = 'blocked';
        recovered.error = 'authorized_scope_no_longer_allowed';
        store.save(recovered);
        continue;
      }
      // Instructions that required durable redaction are intentionally not
      // persisted verbatim. After a process restart the ephemeral original is
      // gone, so never execute the altered/redacted prompt as if it were exact.
      // The owner can submit a fresh continuation instruction instead.
      if (recovered.instructionRecoverable === false && !recovered.execution && !store.hasRuntimeInstruction(recovered.id)) {
        recovered.status = 'blocked';
        recovered.error = 'runtime_instruction_unavailable_after_restart';
        store.save(recovered);
        continue;
      }
      recovered.status = 'queued';
      store.save(recovered);
      queue.enqueue(recovered.id);
    }
  }

  return http.createServer(async (request, response) => {
    const send = (status, value) => { response.writeHead(status, { 'content-type': 'application/json', 'cache-control': 'no-store', 'x-content-type-options': 'nosniff' }); response.end(JSON.stringify(value)); };
    try {
      if (request.method === 'GET' && request.url === '/healthz') return send(200, { status: 'ok', revision: process.env.INQSI_ENGINEERING_SOURCE_SHA || null, execution: 'isolated-ecs' });
      if (browserAuth && await browserAuth(request, response)) return;
      if (request.method === 'GET' && request.url === '/' && browserAuth) {
        try { await authorizer(request); }
        catch { response.writeHead(302, { location: '/auth/login', 'cache-control': 'no-store' }); response.end(); return; }
        response.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store', 'content-security-policy': "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'" });
        return response.end(fs.readFileSync(new URL('../public/index.html', import.meta.url)));
      }
      if (request.method === 'GET' && ['/console.js', '/console.css'].includes(request.url) && browserAuth) {
        await authorizer(request);
        response.writeHead(200, { 'content-type': request.url.endsWith('.js') ? 'text/javascript' : 'text/css', 'x-content-type-options': 'nosniff' });
        return response.end(fs.readFileSync(new URL(`../public${request.url}`, import.meta.url)));
      }
      if (!request.url.startsWith('/v1/engineering')) return send(404, { error: 'not_found' });
      const actor = await authorizer(request);
      const url = new URL(request.url, 'http://localhost'); const parts = url.pathname.split('/').filter(Boolean); const id = parts[2]; const action = parts[3];
      let body = {}; if (['POST','PUT'].includes(request.method)) { let raw=''; for await (const chunk of request) { raw += chunk; if (raw.length > config.maxInstructionBytes + 10000) throw Object.assign(new Error('request_too_large'), { status: 413 }); } try { body = raw ? JSON.parse(raw) : {}; } catch { throw Object.assign(new Error('invalid_json'), { status: 400 }); } }
      if (request.method === 'GET' && !id) return send(200, { jobs: store.list(actor.id).map(publicJob) });
      if (request.method === 'POST' && !id) {
        if (typeof body.instruction !== 'string' || !body.instruction.trim()) return send(400, { error: 'instruction_required' });
        if (!Array.isArray(body.authorizedScope) || !body.authorizedScope.length || body.authorizedScope.some((scope) => !validScope(scope, config.allowedScopes))) return send(400, { error: 'valid_authorized_scope_required' });
        if (body.startingRevision && body.startingRevision !== 'HEAD' && !/^[0-9a-f]{40}$/i.test(body.startingRevision)) return send(400, { error: 'valid_starting_revision_required' });
        let mainRevision;
        try { mainRevision = await refreshRepository(config.repository); }
        catch { return send(503, { error: 'repository_refresh_failed' }); }
        let revision;
        try {
          revision = await resolveRevision(config.repository, body.startingRevision || 'HEAD');
          await assertMainAncestor(config.repository, revision, mainRevision);
        } catch { return send(400, { error: 'starting_revision_not_on_main' }); }
        const job = store.create(body, actor.id, revision); queue.enqueue(job.id); return send(202, { job: publicJob(job) });
      }
      const job = id && store.owned(id, actor.id); if (!job) return send(404, { error: 'job_not_found' });
      if (request.method === 'GET' && !action) return send(200, { job: publicJob(job) });
      if (request.method === 'GET' && action === 'events') { response.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-store', connection: 'keep-alive' }); const push=()=>response.write(`data: ${JSON.stringify(publicJob(store.owned(id, actor.id)))}\n\n`); push(); const interval=setInterval(push, 2000); request.on('close',()=>clearInterval(interval)); return; }
      if (request.method === 'POST' && action === 'cancel') {
        const publicationVisible = Boolean(job.publicationState && job.publicationState !== 'no_changes') || ['awaiting_publication','published'].includes(job.status);
        const cancellableStatus = ['queued','running','awaiting_publication','published'].includes(job.status) || (job.status === 'failed' && publicationVisible);
        if (!cancellableStatus) return send(409, { error: 'job_not_cancellable' });
        // Arbitrate every accepted cancellation before acknowledging it. The
        // publication outbox can become visible before the worker's later job
        // state save; a durable cancellation decision closes that race even
        // when this snapshot still looks like an ordinary running job.
        if (!cancelPublication(config.dataDir, id)) return send(409, { error: 'publication_merge_already_committed' });
        queue.cancel(id);
        if (publicationVisible) markPublicationCancellationPending(store, id, actor.id);
        return send(202, { job: publicJob(store.owned(id, actor.id)) });
      }
      if (request.method === 'POST' && action === 'continue') {
        if (!['completed','failed','blocked','awaiting_approval'].includes(job.status)) return send(409, { error: 'job_not_continuable' });
        if (job.publicationState && job.publicationState !== 'no_changes') return send(409, { error: 'published_job_requires_new_task' });
        if (!validJobScopes(job, config.allowedScopes)) return send(409, { error: 'authorized_scope_no_longer_allowed' });
        if (typeof body.instruction !== 'string' || !body.instruction.trim()) return send(400, { error: 'instruction_required' });
        if (job.execution && !job.execution.stoppedAt) {
          try { await stopExecution(config, job, store); }
          catch { return send(409, { error: 'previous_execution_stop_unconfirmed' }); }
        }
        job.previousExecution = job.execution || job.previousExecution; job.execution = null;
        job.status = 'queued'; job.cancelRequested = false; job.error = null;
        store.saveInstruction(job, body.instruction); queue.enqueue(id); return send(202, { job: publicJob(job) });
      }
      return send(404, { error: 'not_found' });
    } catch (error) {
      if (error.persistencePending && error.jobId) {
        queue.reconcileLater(error.jobId);
        return send(202, { job: { id: error.jobId, status: 'persistence_pending' }, error: 'persistence_outcome_pending_confirmation' });
      }
      send(error.status || 500, { error: error.status ? error.message : 'internal_error' });
    }
  }).on('close', () => queue.close?.());
}

if (process.argv[1] === new URL(import.meta.url).pathname) { const config = loadConfig(); createServer({ config }).listen(config.port, config.bindAddress, () => console.log(`InQsi engineering service listening on ${config.bindAddress}:${config.port}`)); }
