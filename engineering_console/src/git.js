import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import path from 'node:path';

const exec = promisify(execFile);

async function runGit(cwd, args, { trim = true } = {}) {
  const { stdout } = await exec('git', args, { cwd, maxBuffer: 20 * 1024 * 1024 });
  return trim ? stdout.trim() : stdout;
}

export async function git(cwd, args) {
  return runGit(cwd, args);
}

export async function resolveRevision(repo, requested = 'HEAD') {
  return git(repo, ['rev-parse', '--verify', `${requested}^{commit}`]);
}

export async function createWorkspace(config, job) {
  const branch = `inqsi/job-${job.id}`;
  const workspace = path.join(config.workspaceRoot, job.id);
  await exec('mkdir', ['-p', config.workspaceRoot]);
  await git(config.repository, ['worktree', 'add', '-b', branch, workspace, job.startingRevision]);
  return { branch, workspace };
}

function parseNameStatusZ(output) {
  const tokens = output.split('\0');
  const paths = [];
  for (let index = 0; index < tokens.length;) {
    const status = tokens[index++];
    if (!status) break;
    if (status.startsWith('R') || status.startsWith('C')) {
      const from = tokens[index++];
      const to = tokens[index++];
      if (from) paths.push(from);
      if (to) paths.push(to);
    } else {
      const file = tokens[index++];
      if (file) paths.push(file);
    }
  }
  return paths;
}

async function untrackedPatch(workspace, file) {
  try {
    const { stdout } = await exec('git', ['diff', '--no-index', '--binary', '--', '/dev/null', file], {
      cwd: workspace,
      maxBuffer: 20 * 1024 * 1024
    });
    return stdout;
  } catch (error) {
    if (error?.code === 1 && typeof error.stdout === 'string') return error.stdout;
    throw error;
  }
}

export async function collectChanges(workspace, baseRevision = 'HEAD') {
  const trackedStatus = await runGit(workspace, ['diff', '--name-status', '-z', '--find-renames', baseRevision, '--'], { trim: false });
  const untrackedRaw = await runGit(workspace, ['ls-files', '--others', '--exclude-standard', '-z'], { trim: false });
  const untracked = untrackedRaw.split('\0').filter(Boolean);
  const changedFiles = [...new Set([...parseNameStatusZ(trackedStatus), ...untracked])].sort();

  let diff = await runGit(workspace, ['diff', '--binary', '--no-ext-diff', baseRevision, '--'], { trim: false });
  for (const file of untracked) diff += await untrackedPatch(workspace, file);

  return { changedFiles, diff };
}
