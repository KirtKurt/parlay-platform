import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
const origin = process.env.INQSI_ENGINEERING_CONSOLE_URL;
const source = process.env.INQSI_ENGINEERING_SOURCE_SHA;
const issuer = process.env.INQSI_ENGINEERING_OIDC_ISSUER;
export function recoveryPollDecision(recoveryReady, unavailableSince, now = Date.now(), timeoutMs = 180000) {
  if (!recoveryReady) return { retry: false, since: unavailableSince };
  const since = unavailableSince ?? now;
  return { retry: now - since <= timeoutMs, since };
}
async function json(url, options = {}) {
  const r = await fetch(url, { redirect: 'error', signal: AbortSignal.timeout(20000), ...options });
  if (!r.ok) throw Object.assign(new Error(`live_probe_http_${r.status}`), { status: r.status });
  return r.json();
}
async function main() {
  if (!/^https:\/\//.test(origin || '') || !/^https:\/\//.test(issuer || '') || !/^[0-9a-f]{40}$/.test(source || '')) throw new Error('live_probe_configuration_invalid');
  const health = await json(`${origin}/healthz`);
  if (health.revision !== source || health.execution !== 'isolated-ecs' || !/^[0-9a-f-]{36}$/.test(health.instance || '')) throw new Error('deployed_source_mismatch');
  const unauthorized = await fetch(`${origin}/v1/engineering`, { redirect: 'error' });
  if (unauthorized.status !== 401) throw new Error('unauthenticated_api_not_denied');
  const discovery = await json(`${issuer.replace(/\/$/, '')}/.well-known/openid-configuration`);
  if (discovery.issuer !== issuer || !/^https:\/\//.test(discovery.token_endpoint || '')) throw new Error('probe_issuer_mismatch');
  let tokens, expires = 0;
  async function authorization() {
    if (Date.now() + 60000 >= expires) {
      tokens = await json(discovery.token_endpoint, { method: 'POST', headers: { 'content-type': 'application/x-www-form-urlencoded' }, body: new URLSearchParams({ grant_type: 'client_credentials', client_id: process.env.ENG_CONSOLE_PROBE_CLIENT_ID, client_secret: process.env.ENG_CONSOLE_PROBE_CLIENT_SECRET, audience: process.env.INQSI_ENGINEERING_OIDC_AUDIENCE }) });
      if (!tokens.access_token || !Number.isFinite(Number(tokens.expires_in)) || Number(tokens.expires_in) <= 60) throw new Error('probe_access_token_missing_or_short_lived');
      expires = Date.now() + Number(tokens.expires_in) * 1000;
    }
    return { authorization: `Bearer ${tokens.access_token}`, 'content-type': 'application/json' };
  }
  const nonce = crypto.randomBytes(12).toString('hex');
  const proofPath = `engineering_console_publication_proof/runtime-${nonce}.md`;
  const created = await json(`${origin}/v1/engineering`, { method: 'POST', headers: await authorization(), body: JSON.stringify({ instruction: `This is an authorized runtime recovery check. First run the shell command "sleep 90" and wait for it to finish, allowing the controller restart check to observe your active session. Then create ${proofPath} containing exactly a Markdown heading "Runtime proof", source revision ${source}, and nonce ${nonce}. Make no other changes.`, authorizedScope: ['engineering_console_publication_proof'], startingRevision: 'HEAD' }) });
  const deadline = Date.now() + 30 * 60 * 1000;
  let job, ready, recovery;
  const unavailableSince = new Map();
  const pause = () => new Promise(resolve => setTimeout(resolve, 5000));
  async function recoveringRead(url, options) {
    try { const value = await json(url, options); unavailableSince.delete(url); return value; }
    catch (error) {
      // The private ALB may have no healthy controller during its replacement.
      if (ready && (error.status >= 500 || ['TypeError', 'TimeoutError'].includes(error.name))) {
        const decision = recoveryPollDecision(true, unavailableSince.get(url));
        if (!decision.retry) throw new Error('controller_recovery_unavailable_timeout');
        unavailableSince.set(url, decision.since);
        return null;
      }
      throw error;
    }
  }
  while (Date.now() < deadline) {
    const current = await recoveringRead(`${origin}/v1/engineering/${created.job.id}`, { headers: await authorization() });
    if (!current) { await pause(); continue; }
    ({ job } = current);
    if (!ready && job.status === 'running' && job.execution?.taskArn && job.threadId && job.execution.eventOffset > 0) {
      ready = { phase: 'recovery_ready', source, jobId: job.id, executionId: job.execution.id,
        executionTask: job.execution.taskArn, threadId: job.threadId, eventOffset: job.execution.eventOffset, controllerInstance: health.instance };
      console.log(JSON.stringify(ready));
    }
    if (ready && !recovery) {
      const resumed = await recoveringRead(`${origin}/healthz`);
      if (!resumed) { await pause(); continue; }
      if (resumed.revision !== source || resumed.execution !== 'isolated-ecs') throw new Error('recovered_source_mismatch');
      if (resumed.instance !== health.instance) {
        const evidence = job.controllerRecovery;
        if (evidence?.instance === resumed.instance) {
          if (evidence.executionId !== ready.executionId || evidence.taskArn !== ready.executionTask ||
              evidence.threadId !== ready.threadId || evidence.eventOffset < ready.eventOffset ||
              job.execution.id !== ready.executionId || job.execution.taskArn !== ready.executionTask ||
              job.threadId !== ready.threadId || job.execution.eventOffset < evidence.eventOffset) throw new Error('active_job_recovery_identity_mismatch');
          recovery = { previousInstance: health.instance, instance: resumed.instance, executionId: ready.executionId,
            threadId: ready.threadId, eventOffset: job.execution.eventOffset, checkpointOffset: evidence.eventOffset };
        }
      }
    }
    if (job.publicationState === 'merged' && job.status === 'completed') break;
    if (['failed', 'blocked', 'cancelled'].includes(job.status)) throw new Error(`controlled_job_${job.status}`);
    await pause();
  }
  if (job?.publicationState !== 'merged' || !job.mergeCommit || !job.execution?.taskArn) throw new Error('controlled_job_merge_not_verified');
  if (!recovery || job.execution.taskArn !== ready.executionTask || job.execution.id !== ready.executionId) throw new Error('active_job_restart_not_verified');
  const match = /^https:\/\/github.com\/KirtKurt\/parlay-platform\/pull\/(\d+)$/.exec(job.pullRequest || '');
  if (!match) throw new Error('proof_pr_identity_missing');
  const pr = await json(`https://api.github.com/repos/KirtKurt/parlay-platform/pulls/${match[1]}`);
  if (!pr.merged || pr.merge_commit_sha !== job.mergeCommit || pr.head.sha !== job.commit) throw new Error('proof_github_merge_mismatch');
  const files = await json(`https://api.github.com/repos/KirtKurt/parlay-platform/pulls/${match[1]}/files?per_page=100`);
  if (files.length !== 1 || files[0].filename !== proofPath || files[0].status !== 'added') throw new Error('proof_file_set_mismatch');
  const file = await json(`https://api.github.com/repos/KirtKurt/parlay-platform/contents/${proofPath}?ref=${job.mergeCommit}`);
  const content = Buffer.from(file.content || '', 'base64').toString('utf8');
  if (!content.includes(source) || !content.includes(nonce) || !/^# Runtime proof\s*$/m.test(content)) throw new Error('proof_content_mismatch');
  console.log(JSON.stringify({ verified: true, source, jobId: job.id, executionId: job.execution.id, executionTask: job.execution.taskArn, recovery, pullRequest: job.pullRequest, commit: job.commit, mergeCommit: job.mergeCommit, checkedAt: new Date().toISOString() }));
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main().catch(error => { console.error(String(error.message).replace(/[^a-zA-Z0-9_:-]/g, '_')); process.exitCode = 1; });
}
