import { EcsCodex, stopExecution } from './ecs-job.js';
import { createWorkspace, collectChanges, git } from './git.js';
import { sanitize } from './sanitize.js';
import { withinAuthorizedScope, writePublicationRequest } from './publication.js';

export function shouldStopCancelledExecution(job, isolatedRuntime = true) {
  return isolatedRuntime && Boolean(job?.cancelRequested && job?.execution && !job.execution.stoppedAt);
}

export function createRunner(config, store, CodexClass = null) {
  return async (job, signal) => {
    let workspace;
    try {
      job.status = 'running';
      job.error = null;
      await store.saveAsync(job);

      if (shouldStopCancelledExecution(job, !CodexClass)) {
        // A recovered execution may have a durable execution id but no taskArn
        // because the controller lost the RunTask response. stopExecution owns
        // task discovery and must run before any relaunch can occur.
        await stopExecution(config, job, store);
        job.status = 'cancelled'; await store.saveAsync(job); return;
      }

      ({ workspace, branch: job.branch } = await createWorkspace(config, job));
      await store.saveAsync(job);

      const codex = CodexClass ? new CodexClass() : new EcsCodex({ config, job, workspace, store });
      const threadOptions = {
        workingDirectory: workspace,
        skipGitRepoCheck: false,
        sandboxMode: 'workspace-write',
        networkAccessEnabled: false,
        webSearchMode: 'disabled',
        approvalPolicy: 'never'
      };
      const thread = job.threadId ? codex.resumeThread(job.threadId, threadOptions) : codex.startThread(threadOptions);

      const prompt = `Authorized repository: KirtKurt/parlay-platform\nAuthorized paths: ${job.authorizedScope.join(', ')}\nDo not modify files outside those paths. Keep secrets out of code and output. Publishing and deployment authority are not available to this coding worker.\n\n${job.instruction}`;
      const { events } = await thread.runStreamed(prompt, { signal });

      let sawEvent = false;
      let turnCompleted = false;
      let turnFailure = null;

      for await (const event of events) {
        sawEvent = true;
        if (event.type === 'thread.started') job.threadId = event.thread_id || thread.id || job.threadId;
        else job.threadId = thread.id || job.threadId;

        const item = event.item;
        const message = item?.text || item?.aggregated_output || event.error?.message || event.message?.content?.[0]?.text || event.type;
        job.logs.push(sanitize(message));
        job.logs = job.logs.slice(-500);

        if (event.type === 'item.completed' && item?.type === 'command_execution') {
          job.testResults.push({
            command: sanitize(item.command),
            exitCode: item.exit_code,
            output: sanitize(item.aggregated_output || ''),
            status: item.status || null
          });
        }
        if (event.type === 'turn.completed') turnCompleted = true;
        if (event.type === 'turn.failed') turnFailure = event.error?.message || 'codex_turn_failed';
        await store.saveAsync(job);
      }

      if (signal.aborted || job.cancelRequested) {
        job.status = 'cancelled';
        await store.saveAsync(job);
        return;
      }
      if (turnFailure) throw new Error(turnFailure);
      if (!sawEvent) throw new Error('codex_event_stream_empty');
      if (!turnCompleted) throw new Error('codex_turn_incomplete');

      const result = await collectChanges(workspace, job.startingRevision);
      job.changedFiles = result.changedFiles;
      job.diff = result.diff;

      const outside = result.changedFiles.filter((file) => !withinAuthorizedScope(file, job.authorizedScope));
      if (outside.length) throw new Error(`scope_violation:${outside.join(',')}`);

      const head = await git(workspace, ['rev-parse', 'HEAD']);
      if (head !== job.startingRevision) job.commit = head;

      if (signal.aborted || job.cancelRequested) {
        job.status = 'cancelled';
        await store.saveAsync(job);
        return;
      }

      if (!result.changedFiles.length) {
        job.status = 'completed';
        job.publicationState = 'no_changes';
      } else {
        writePublicationRequest(config, job, result.diff, signal);
        job.status = 'awaiting_publication';
        job.publicationState = 'queued';
      }
      await store.saveAsync(job);
    } catch (error) {
      if (['ESTALE', 'EWRITEUNKNOWN', 'EBUSY'].includes(error?.code)) throw error;
      if (error?.code === 'EFBIG') {
        // Do not retry the same oversized in-memory record while recording its
        // failure. Start with the last confirmed snapshot and retain its data.
        const latest = store.get(job.id);
        if (latest && !['merged', 'merge_conflict'].includes(latest.publicationState) &&
            !['completed', 'cancelled'].includes(latest.status)) {
          latest.status = 'blocked'; latest.error = 'job_store_record_too_large';
          await store.saveAsync(latest);
        }
        return;
      }
      job.status = job.execution && !job.execution.stoppedAt ? 'blocked' : signal.aborted || job.cancelRequested ? 'cancelled' : 'failed';
      job.error = sanitize(error?.message || error);
      await store.saveAsync(job);
    }
  };
}
