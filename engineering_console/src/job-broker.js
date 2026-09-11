import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { Readable } from 'node:stream';
import { authenticateTask, atomicTransportWrite, MAX_TRANSPORT_BYTES } from './task-transport.js';

async function readBody(request, limit) {
  let size = 0; const chunks = [];
  for await (const chunk of request) { size += chunk.length; if (size > limit) throw new Error('body_too_large'); chunks.push(chunk); }
  return Buffer.concat(chunks);
}
export function createJobBroker({ root, openAIKey, model, upstream = fetch } = {}) {
  if (!path.isAbsolute(root || '') || !openAIKey || !model) throw new Error('broker_configuration_required');
  fs.mkdirSync(root, { recursive: true, mode: 0o700 });
  return http.createServer(async (request, response) => {
    const send = (code, body) => { response.writeHead(code, { 'content-type': 'application/json', 'cache-control': 'no-store' }); response.end(JSON.stringify(body)); };
    try {
      if (request.method === 'GET' && request.url === '/healthz') return send(200, { status: 'ok', role: 'broker' });
      let task;
      try { task = authenticateTask(root, request.headers.authorization); }
      catch { return send(401, { error: 'task_authentication_required' }); }
      const url = new URL(request.url, 'http://broker.invalid');
      if (url.search) return send(400, { error: 'query_not_allowed' });
      const match = /^\/broker\/transport\/([0-9a-f-]{36})\/(input|checkpoint|result)$/.exec(url.pathname);
      if (match) {
        if (match[1] !== task.id) return send(403, { error: 'task_scope_mismatch' });
        const name = match[2];
        if (name === 'input' && request.method === 'GET') {
          response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' });
          return fs.createReadStream(path.join(task.directory, 'input.json')).pipe(response);
        }
        if (['checkpoint', 'result'].includes(name) && request.method === 'PUT') {
          const body = await readBody(request, MAX_TRANSPORT_BYTES);
          // Uploads can outlive cancellation, expiry or token rotation. Re-read
          // the capability immediately before accepting durable task output.
          try { task = authenticateTask(root, request.headers.authorization); }
          catch { return send(401, { error: 'task_authentication_required' }); }
          const value = JSON.parse(body);
          if (typeof value !== 'object' || !value || Array.isArray(value)) return send(400, { error: 'invalid_task_result' });
          try { atomicTransportWrite(path.join(task.directory, `${name}.json`), body, { immutable: name === 'result' }); }
          catch (error) { if (error.code === 'EEXIST') return send(409, { error: 'result_already_committed' }); throw error; }
          return send(201, { stored: true });
        }
        return send(405, { error: 'method_not_allowed' });
      }
      if (url.pathname === '/broker/v1/models' && request.method === 'GET') return send(200, { object: 'list', data: [{ id: model, object: 'model', owned_by: 'openai' }] });
      if (url.pathname !== '/broker/v1/responses' || request.method !== 'POST') return send(404, { error: 'broker_route_not_allowed' });
      const body = JSON.parse(await readBody(request, 8 * 1024 * 1024));
      // Never start a new model operation using authorization captured before
      // awaiting a potentially slow body. In-flight upstream calls are separate.
      try { task = authenticateTask(root, request.headers.authorization); }
      catch { return send(401, { error: 'task_authentication_required' }); }
      if (body.model !== model || (body.tools || []).some(t => !['function', 'custom', 'local_shell'].includes(t.type))) return send(403, { error: 'model_or_tool_not_allowed' });
      const countPath = path.join(task.directory, 'requests.json');
      let count = 0;
      try { count = JSON.parse(fs.readFileSync(countPath, 'utf8')).count; } catch (e) { if (e.code !== 'ENOENT') throw e; }
      if (!Number.isSafeInteger(count) || count >= task.auth.maxRequests) return send(429, { error: 'job_request_limit' });
      atomicTransportWrite(countPath, JSON.stringify({ count: count + 1 }));
      body.store = false;
      body.max_output_tokens = Math.min(Number(body.max_output_tokens) || 32768, 32768);
      // Upstream origin, path and headers are fixed. Jobs never receive the key,
      // cannot select a proxy target, and cannot invoke unrelated account APIs.
      const result = await upstream('https://api.openai.com/v1/responses', {
        method: 'POST', redirect: 'error', signal: AbortSignal.timeout(120000),
        headers: { authorization: `Bearer ${openAIKey}`, 'content-type': 'application/json' }, body: JSON.stringify(body)
      });
      if (!result.ok) return send(result.status, { error: 'upstream_request_rejected' });
      response.writeHead(result.status, { 'content-type': result.headers.get('content-type') || 'application/json', 'cache-control': 'no-store' });
      if (result.body) Readable.fromWeb(result.body).on('error', () => response.destroy()).pipe(response);
      else response.end();
    } catch {
      if (!response.headersSent) send(502, { error: 'broker_request_failed' });
      else response.destroy();
    }
  });
}
