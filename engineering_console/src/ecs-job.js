import fs from 'node:fs';
import path from 'node:path';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { aws } from './aws-cli.js';
import { createTaskTransport, transportDirectory, atomicTransportWrite } from './task-transport.js';
import { git } from './git.js';
import { validatePublisherRequest, loadPublicationPolicy, isProofFile } from './publication-policy.js';
import crypto from 'node:crypto';
const exec = promisify(execFile);
async function persistJob(store, job) { return store.saveAsync ? store.saveAsync(job) : store.save(job); }

export function validateReturnedPatch(patch, changedFiles) {
  // The manifest is untrusted. Verify the actual Git headers before any write
  // into the controller checkout. proof-v1 permits regular Markdown files only.
  const files = [];
  for (const line of patch.split('\n')) {
    if (line.startsWith('diff --git ')) {
      const match = /^diff --git a\/(\S+) b\/(\S+)$/.exec(line);
      if (!match || match[1] !== match[2] || !isProofFile(match[1])) throw new Error('returned_patch_path_forbidden');
      files.push(match[1]);
    }
    if (/^(old mode|new mode|new file mode|deleted file mode) /.test(line) && !line.endsWith(' 100644')) throw new Error('returned_patch_mode_forbidden');
    if (/^(GIT binary patch|Binary files |rename from |rename to |copy from |copy to )/.test(line)) throw new Error('returned_patch_format_forbidden');
  }
  if (!files.length || new Set(files).size !== files.length || JSON.stringify(files.sort()) !== JSON.stringify([...changedFiles].sort())) throw new Error('returned_patch_manifest_mismatch');
}

export async function stopExecution(config, job, store, callAws = aws, { pollMs = 2000, attempts = 75 } = {}) {
  if (!job.execution || job.execution.stoppedAt) return;
  const authFile = path.join(transportDirectory(config.transportDir, job.execution.id), 'auth.json');
  const auth = JSON.parse(fs.readFileSync(authFile, 'utf8'));
  atomicTransportWrite(authFile, JSON.stringify({ ...auth, expires: 0 }));
  try {
    if (!job.execution.taskArn) {
      const found = await callAws('ecs', 'list-tasks', { cluster: config.cluster, startedBy: job.execution.id });
      if (found.taskArns?.length !== 1) throw new Error('isolated_job_stop_unconfirmed');
      job.execution.taskArn = found.taskArns[0]; await persistJob(store, job);
    }
    await callAws('ecs', 'stop-task', { cluster: config.cluster, task: job.execution.taskArn, reason: 'Engineering Console execution ended' });
    for (let attempt = 0; attempt < attempts; attempt++) {
      const result = await callAws('ecs', 'describe-tasks', { cluster: config.cluster, tasks: [job.execution.taskArn] });
      if (!result.failures?.length && result.tasks?.[0]?.taskArn === job.execution.taskArn && result.tasks[0].lastStatus === 'STOPPED') {
        job.execution.stoppedAt = new Date().toISOString(); await persistJob(store, job); return;
      }
      await new Promise(resolve => setTimeout(resolve, pollMs));
    }
  } catch { throw new Error('isolated_job_stop_unconfirmed'); }
  throw new Error('isolated_job_stop_unconfirmed');
}

export function validateIsolatedTaskDefinition(definition, expectedImage) {
  const task = definition.taskDefinition || definition;
  if (!task.family?.startsWith('eng-console-') || task.networkMode !== 'awsvpc' || !task.requiresCompatibilities?.includes('FARGATE') || task.taskRoleArn || task.pidMode || task.ipcMode) throw new Error('job_task_boundary_invalid');
  if ((task.volumes || []).some(v => v.efsVolumeConfiguration || v.host || v.fsxWindowsFileServerVolumeConfiguration || v.dockerVolumeConfiguration)) throw new Error('job_shared_volume_forbidden');
  if (task.containerDefinitions?.length !== 1) throw new Error('job_task_must_be_single_container');
  const c = task.containerDefinitions[0];
  if (c.name !== 'job' || c.image !== expectedImage || c.privileged || c.readonlyRootFilesystem !== true || c.secrets?.length || c.environmentFiles?.length || c.user !== '10001:10001' || c.linuxParameters?.capabilities?.add?.length || c.mountPoints?.some(p => !['/job', '/tmp'].includes(p.containerPath))) throw new Error('job_container_boundary_invalid');
  if ((c.environment || []).some(e => /TOKEN|KEY|SECRET|PASSWORD|CREDENTIAL/i.test(e.name))) throw new Error('job_static_credential_forbidden');
  if (JSON.stringify(c.command || []) !== JSON.stringify(['node', '/app/scripts/isolated-job.mjs']) || JSON.stringify(c.entryPoint || []) !== JSON.stringify(['/usr/bin/tini', '--'])) throw new Error('job_entrypoint_invalid');
  return task;
}

export class EcsCodex {
  constructor({ config, job, workspace, store, callAws = aws, pollMs = 2000 }) { Object.assign(this, { config, job, workspace, store, callAws, pollMs }); }
  startThread(options) { return this.thread(null, options); }
  resumeThread(id, options) { return this.thread(id, options); }
  thread(id, options) {
    const owner = this;
    return { id, async runStreamed(prompt, { signal }) { return { events: owner.run(prompt, id, options, signal) }; } };
  }
  async *run(prompt, threadId, options, signal) {
    const { config, job, store, callAws } = this;
    for (const key of ['cluster', 'jobTaskDefinition', 'jobImage', 'jobSecurityGroup', 'brokerUrl', 'transportDir', 'model']) if (!config[key]) throw new Error(`isolated_runner_missing_${key}`);
    if (!/^https:\/\//.test(config.brokerUrl) || !Array.isArray(config.jobSubnets) || config.jobSubnets.length < 2) throw new Error('isolated_runner_network_invalid');
    validateIsolatedTaskDefinition(await callAws('ecs', 'describe-task-definition', { taskDefinition: config.jobTaskDefinition }), config.jobImage);
    fs.mkdirSync(config.transportDir, { recursive: true, mode: 0o700 });
    if (!job.execution) {
      if (signal.aborted || job.cancelRequested) return;
      if (job.previousExecution && !job.previousExecution.stoppedAt) throw new Error('previous_execution_stop_unconfirmed');
      const archive = await exec('git', ['archive', '--format=tar.gz', job.startingRevision], { cwd: config.repository, encoding: 'buffer', maxBuffer: 40 * 1024 * 1024 });
      let checkpoint = null;
      if (job.previousExecution) {
        const prior = transportDirectory(config.transportDir, job.previousExecution.id);
        for (const name of ['result.json', 'checkpoint.json']) { try { checkpoint = JSON.parse(fs.readFileSync(path.join(prior, name), 'utf8')); break; } catch (e) { if (e.code !== 'ENOENT') throw e; } }
      }
      const transport = createTaskTransport(config.transportDir, { prompt, instruction: job.instruction, jobId: job.id, startingRevision: job.startingRevision, authorizedScope: job.authorizedScope, archive: archive.stdout.toString('base64'), model: config.model, threadId, options }, checkpoint);
      job.execution = { id: transport.id, requestedAt: new Date().toISOString(), taskArn: null };
      await persistJob(store, job);
    }
    const directory = transportDirectory(config.transportDir, job.execution.id);
    const token = JSON.parse(fs.readFileSync(path.join(directory, 'auth.json'), 'utf8')).token;
    const readResult = () => { try { return JSON.parse(fs.readFileSync(path.join(directory, 'result.json'), 'utf8')); } catch (e) { if (e.code === 'ENOENT') return null; throw e; } };
    if (!job.execution.taskArn) {
      if (Date.now() >= Date.parse(job.execution.requestedAt) + 30 * 60 * 1000) throw new Error('isolated_job_launch_recovery_expired');
      // Stable clientToken makes recovery of a lost RunTask response idempotent.
      const launch = { cluster: config.cluster, taskDefinition: config.jobTaskDefinition, launchType: 'FARGATE', platformVersion: '1.4.0', clientToken: job.execution.id, count: 1, startedBy: job.execution.id, enableExecuteCommand: false,
        networkConfiguration: { awsvpcConfiguration: { subnets: config.jobSubnets, securityGroups: [config.jobSecurityGroup], assignPublicIp: 'DISABLED' } },
        overrides: { containerOverrides: [{ name: 'job', environment: [{ name: 'ENG_CONSOLE_BROKER_URL', value: config.brokerUrl }, { name: 'ENG_CONSOLE_JOB_TOKEN', value: token }, { name: 'ENG_CONSOLE_EXECUTION_ID', value: job.execution.id }] }] }
      };
      let result;
      for (let attempt = 0; attempt < 3; attempt++) {
        try { result = await callAws('ecs', 'run-task', launch); break; }
        catch {
          if (attempt === 2) {
            // Discover an accepted launch whose response remained unavailable.
            // If discovery is also unavailable, revoke capability and leave the
            // execution blocked for explicit stop/reconciliation, never rerun it.
            try {
              const found = await callAws('ecs', 'list-tasks', { cluster: config.cluster, startedBy: job.execution.id });
              if (found.taskArns?.length !== 1) throw new Error();
              result = { tasks: [{ taskArn: found.taskArns[0] }] };
            } catch {
              const authFile = path.join(directory, 'auth.json');
              const auth = JSON.parse(fs.readFileSync(authFile, 'utf8'));
              atomicTransportWrite(authFile, JSON.stringify({ ...auth, expires: 0 }));
              throw new Error('isolated_job_launch_unconfirmed');
            }
          } else await new Promise(resolve => setTimeout(resolve, this.pollMs));
        }
      }
      if (result.failures?.length || result.tasks?.length !== 1 || !result.tasks[0].taskArn) throw new Error('isolated_job_launch_failed');
      job.execution.taskArn = result.tasks[0].taskArn;
      await persistJob(store, job);
    }
    let stopped = false;
    const pendingEvents = function*(output) {
      if (!Array.isArray(output?.events)) return;
      const total = output.eventCount ?? output.events.length;
      if (!Number.isSafeInteger(total) || total < output.events.length) throw new Error('invalid_checkpoint_event_count');
      const start = total - output.events.length;
      for (let index = 0; index < output.events.length; index++) {
        const offset = start + index + 1;
        if (offset <= (job.execution.eventOffset || 0)) continue;
        job.execution.eventOffset = offset;
        yield output.events[index];
      }
    };
    const deadline = Date.parse(job.execution.requestedAt) + 30 * 60 * 1000;
    try {
      for (;;) {
        if (signal.aborted || job.cancelRequested || Date.now() >= deadline) throw new Error(signal.aborted || job.cancelRequested ? 'job_cancelled' : 'isolated_job_timeout');
        const result = await callAws('ecs', 'describe-tasks', { cluster: config.cluster, tasks: [job.execution.taskArn] });
        const task = result.tasks?.[0];
        if (result.failures?.length || !task || task.taskArn !== job.execution.taskArn) throw new Error('isolated_job_state_unverified');
        if (task.lastStatus === 'STOPPED') {
          stopped = true;
          job.execution.stoppedAt = new Date().toISOString(); await persistJob(store, job);
          const output = readResult();
          if (task.containers?.length !== 1 || task.containers[0].exitCode !== 0 || !output || output.completed !== true || !Array.isArray(output.events) || !output.events.some(e => e.type === 'turn.completed') || output.events.some(e => e.type === 'turn.failed')) throw new Error('isolated_job_failed_or_checkpoint_only');
          const patch = String(output.diff || '');
          if (patch) {
            const manifest = { version: 1, jobId: job.id, repository: 'KirtKurt/parlay-platform', branch: `inqsi/publish-${job.id}`, startingRevision: job.startingRevision, authorizedScope: job.authorizedScope, changedFiles: output.changedFiles, requiredChecks: config.requiredChecks, patchSha256: crypto.createHash('sha256').update(patch).digest('hex') };
            validatePublisherRequest(manifest, patch, loadPublicationPolicy({ INQSI_ENGINEERING_PUBLICATION_POLICY: 'proof-v1', INQSI_ENGINEERING_ALLOWED_SCOPES: config.allowedScopes.join(','), INQSI_ENGINEERING_REQUIRED_CHECKS: config.requiredChecks.join(',') }));
            validateReturnedPatch(patch, output.changedFiles);
            const patchPath = path.join(directory, 'verified.patch');
            fs.writeFileSync(patchPath, patch, { mode: 0o600 });
            const stat = await exec('git', ['apply', '--numstat', '-z', patchPath], { cwd: this.workspace });
            const actual = stat.stdout.split('\0').filter(Boolean).map(line => {
              const match = /^\d+\t\d+\t(.+)$/.exec(line);
              if (!match || !isProofFile(match[1])) throw new Error('returned_patch_actual_path_forbidden');
              return match[1];
            }).sort();
            if (JSON.stringify(actual) !== JSON.stringify([...output.changedFiles].sort())) throw new Error('returned_patch_actual_manifest_mismatch');
            // A controller may have crashed after applying this immutable result.
            // Reconciliation must not apply the same patch twice.
            const current = await git(this.workspace, ['diff', '--binary', '--full-index', '--no-ext-diff', '--no-textconv', job.startingRevision, '--']);
            if (current.trim() !== patch.trim()) {
              if (current) throw new Error('controller_workspace_requires_reconciliation');
              await git(this.workspace, ['apply', '--check', patchPath]);
              await git(this.workspace, ['apply', '--index', patchPath]);
            }
          }
          if (output.threadId) yield { type: 'thread.started', thread_id: output.threadId };
          for (const event of pendingEvents(output)) yield event;
          // Recovery may already have persisted every checkpoint event. A fresh
          // runner still needs the verified terminal event after materialization.
          yield { type: 'turn.completed' };
          return;
        }
        let checkpoint;
        try { checkpoint = JSON.parse(fs.readFileSync(path.join(directory, 'checkpoint.json'), 'utf8')); }
        catch (error) { if (error.code !== 'ENOENT') throw error; }
        for (const event of pendingEvents(checkpoint)) yield event;
        await new Promise(resolve => setTimeout(resolve, this.pollMs));
      }
    } finally {
      if (!stopped) await stopExecution(config, job, store, callAws, { pollMs: this.pollMs });
      // Expire the job capability. Checkpoints remain durable and are restored
      // only inside a later isolated task, never extracted by a trusted service.
      const authFile = path.join(directory, 'auth.json');
      const auth = JSON.parse(fs.readFileSync(authFile, 'utf8')); auth.expires = 0;
      atomicTransportWrite(authFile, JSON.stringify(auth));
    }
  }
}
