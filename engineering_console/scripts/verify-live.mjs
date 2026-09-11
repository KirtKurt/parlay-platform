import crypto from 'node:crypto';
const origin = process.env.INQSI_ENGINEERING_CONSOLE_URL;
const source = process.env.INQSI_ENGINEERING_SOURCE_SHA;
const issuer = process.env.INQSI_ENGINEERING_OIDC_ISSUER;
if (!/^https:\/\//.test(origin || '') || !/^https:\/\//.test(issuer || '') || !/^[0-9a-f]{40}$/.test(source || '')) throw new Error('live_probe_configuration_invalid');
async function json(url, options = {}) {
  const r = await fetch(url, { redirect: 'error', signal: AbortSignal.timeout(20000), ...options });
  if (!r.ok) throw new Error(`live_probe_http_${r.status}`);
  return r.json();
}
try {
  const health = await json(`${origin}/healthz`);
  if (health.revision !== source || health.execution !== 'isolated-ecs') throw new Error('deployed_source_mismatch');
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
  const instruction = `First execute the shell command sleep 60 and wait for it to finish. Only then create ${proofPath} containing exactly a Markdown heading "Runtime proof", source revision ${source}, and nonce ${nonce}. Make no other changes.`;
  const created = await json(`${origin}/v1/engineering`, { method: 'POST', headers: await authorization(), body: JSON.stringify({ instruction, authorizedScope: ['engineering_console_publication_proof'], startingRevision: 'HEAD' }) });
  const deadline = Date.now() + 30 * 60 * 1000;
  let job;
  let recoveryReady = false;
  while (Date.now() < deadline) {
    ({ job } = await json(`${origin}/v1/engineering/${created.job.id}`, { headers: await authorization() }));
    if (!recoveryReady && job.status === 'running' && job.execution?.id && job.execution?.taskArn) {
      console.log(JSON.stringify({ recoveryReady: true, source, jobId: job.id, executionId: job.execution.id, executionTask: job.execution.taskArn, observedAt: new Date().toISOString() }));
      recoveryReady = true;
    }
    if (job.publicationState === 'merged' && job.status === 'completed') break;
    if (['failed', 'blocked', 'cancelled'].includes(job.status)) throw new Error(`controlled_job_${job.status}`);
    await new Promise(resolve => setTimeout(resolve, recoveryReady ? 5000 : 1000));
  }
  if (!recoveryReady) throw new Error('recovery_probe_never_observed_active_execution');
  if (job?.publicationState !== 'merged' || !job.mergeCommit || !job.execution?.taskArn || !job.execution?.id) throw new Error('controlled_job_merge_not_verified');
  const match = /^https:\/\/github.com\/KirtKurt\/parlay-platform\/pull\/(\d+)$/.exec(job.pullRequest || '');
  if (!match) throw new Error('proof_pr_identity_missing');
  const pr = await json(`https://api.github.com/repos/KirtKurt/parlay-platform/pulls/${match[1]}`);
  if (!pr.merged || pr.merge_commit_sha !== job.mergeCommit || pr.head.sha !== job.commit) throw new Error('proof_github_merge_mismatch');
  const files = await json(`https://api.github.com/repos/KirtKurt/parlay-platform/pulls/${match[1]}/files?per_page=100`);
  if (files.length !== 1 || files[0].filename !== proofPath || files[0].status !== 'added') throw new Error('proof_file_set_mismatch');
  const file = await json(`https://api.github.com/repos/KirtKurt/parlay-platform/contents/${proofPath}?ref=${job.mergeCommit}`);
  const content = Buffer.from(file.content || '', 'base64').toString('utf8');
  if (!content.includes(source) || !content.includes(nonce) || !/^# Runtime proof\s*$/m.test(content)) throw new Error('proof_content_mismatch');
  console.log(JSON.stringify({ verified: true, source, jobId: job.id, executionId: job.execution.id, executionTask: job.execution.taskArn, pullRequest: job.pullRequest, commit: job.commit, mergeCommit: job.mergeCommit, checkedAt: new Date().toISOString() }));
} catch (error) { console.error(String(error.message).replace(/[^a-zA-Z0-9_:-]/g, '_')); process.exitCode = 1; }
