import { Codex } from '@openai/codex-sdk';
import { createWorkspace, collectChanges, git } from './git.js';
import { sanitize } from './sanitize.js';
import { withinAuthorizedScope, writePublicationRequest } from './publication.js';
import path from 'node:path';

export function createRunner(config, store, CodexClass = Codex) {
  return async (job, signal) => {
    let workspace;
    try {
      job.status = 'running';
      job.error = null;
      store.save(job);

      if (job.branch) workspace = path.join(config.workspaceRoot, job.id);
      else ({ workspace, branch: job.branch } = await createWorkspace(config, job));
      store.save(job);

      const codex = new CodexClass();
      const threadOptions = {
        workingDirectory: workspace,
        skipGitRepoCheck: false,
        sandboxMode: 'workspace-write',
        networkAccessEnabled: false,
        webSearchMode: 'disabled',
        approvalPolicy: 'never'
      };
      const thread = job.threadId ? codex.resumeThread(job.threadId, threadOptions) : codex.startThread(threadOptions);

      const prompt = `Authorized repository: KirtKurt/parlay-platform\nAuthorized paths: ${job.authorizedScope.join(', ')}\nDo not modify files outside those paths. Keep credentials out of code and output. Publishing and deployment credentials are not available to this coding worker.\n\n${job.instruction}`;
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
        store.save(job);
      }

      if (signal.aborted || job.cancelRequested) {
        job.status = 'cancelled';
        store.save(job);
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

      if (!result.changedFiles.length) {
        job.status = 'completed';
        job.publicationState = 'no_changes';
      } else {
        writePublicationRequest(config, job, result.diff);
        job.status = 'awaiting_publication';
        job.publicationState = 'queued';
      }
      store.save(job);
    } catch (error) {
      job.status = signal.aborted || job.cancelRequested ? 'cancelled' : 'failed';
      job.error = sanitize(error?.message || error);
      store.save(job);
    }
  };
}
