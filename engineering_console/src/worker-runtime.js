import { Codex } from '@openai/codex-sdk';
import { createWorkspace, collectChanges, git } from './git.js';
import { sanitize } from './sanitize.js';
import { writePublishBundle } from './publish-bundle.js';
import path from 'node:path';

function normalizeRepoPath(value) {
  return String(value || '').replaceAll('\\', '/').replace(/^\.\//, '').replace(/\/$/, '');
}

function isWithinScope(file, scopes) {
  const normalized = normalizeRepoPath(file);
  if (!normalized || normalized === '.' || normalized.startsWith('/') || normalized.split('/').some((part) => part === '..' || part === '.git')) return false;
  return scopes.some((scope) => {
    const allowed = normalizeRepoPath(scope);
    return normalized === allowed || normalized.startsWith(`${allowed}/`);
  });
}

function codexEnvironment(env = process.env) {
  const safeNames = ['HOME', 'PATH', 'TMPDIR', 'LANG', 'LC_ALL', 'SHELL', 'USER'];
  return Object.fromEntries(safeNames.filter((name) => env[name]).map((name) => [name, env[name]]));
}

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

      const codex = new CodexClass({
        apiKey: process.env.OPENAI_API_KEY || process.env.CODEX_API_KEY,
        env: codexEnvironment()
      });
      const threadOptions = {
        workingDirectory: workspace,
        skipGitRepoCheck: false,
        sandboxMode: 'workspace-write',
        networkAccessEnabled: false,
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

      const outside = result.changedFiles.filter((file) => !isWithinScope(file, job.authorizedScope));
      if (outside.length) throw new Error(`scope_violation:${outside.join(',')}`);

      const head = await git(workspace, ['rev-parse', 'HEAD']);
      if (head !== job.startingRevision) job.commit = head;

      const bundle = config.outboxDir ? writePublishBundle(config, job, result.diff) : null;
      job.diff = result.diff;
      if (bundle) {
        job.publishState = 'queued';
        job.publishPatchSha256 = bundle.patchSha256;
        job.status = 'awaiting_publish';
      } else {
        job.status = 'completed';
      }
      store.save(job);
    } catch (error) {
      job.status = signal.aborted || job.cancelRequested ? 'cancelled' : 'failed';
      job.error = sanitize(error?.message || error);
      store.save(job);
    }
  };
}
