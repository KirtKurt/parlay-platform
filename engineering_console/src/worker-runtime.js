import { Codex } from '@openai/codex-sdk';
import { createWorkspace, collectChanges, git } from './git.js';
import { sanitize } from './sanitize.js';
import path from 'node:path';

export function createRunner(config, store, CodexClass = Codex) {
  return async (job, signal) => {
    let workspace;
    try {
      job.status = 'running'; store.save(job);
      if (job.branch) workspace = path.join(config.workspaceRoot, job.id);
      else ({ workspace, branch: job.branch } = await createWorkspace(config, job));
      store.save(job);
      const codex = new CodexClass();
      const thread = job.threadId ? codex.resumeThread(job.threadId) : codex.startThread({ workingDirectory: workspace, skipGitRepoCheck: false });
      job.threadId = thread.id || job.threadId; store.save(job);
      const prompt = `Authorized repository: KirtKurt/parlay-platform\nAuthorized paths: ${job.authorizedScope.join(', ')}\nDo not modify files outside those paths. Do not deploy or merge.\n\n${job.instruction}`;
      const { events } = await thread.runStreamed(prompt, { signal });
      for await (const event of events) {
        const item = event.item;
        const message = item?.text || item?.aggregated_output || event.message?.content?.[0]?.text || event.type;
        job.logs.push(sanitize(message)); job.logs = job.logs.slice(-500);
        if (event.type === 'item.completed' && item?.type === 'command_execution') job.testResults.push({ command: sanitize(item.command), exitCode: item.exit_code, output: sanitize(item.aggregated_output || '') });
        job.threadId = thread.id || job.threadId; store.save(job);
      }
      if (signal.aborted || job.cancelRequested) { job.status = 'cancelled'; store.save(job); return; }
      const result = await collectChanges(workspace); job.changedFiles = result.changedFiles; job.diff = result.diff;
      const outside = result.changedFiles.filter((file) => !job.authorizedScope.some((scope) => file === scope || file.startsWith(`${scope.replace(/\/$/, '')}/`)));
      if (outside.length) throw new Error(`scope_violation:${outside.join(',')}`);
      const head = await git(workspace, ['rev-parse', 'HEAD']);
      if (head !== job.startingRevision) job.commit = head;
      job.status = 'completed'; store.save(job);
    } catch (error) { job.status = signal.aborted ? 'cancelled' : 'failed'; job.error = sanitize(error?.message || error); store.save(job); }
  };
}
