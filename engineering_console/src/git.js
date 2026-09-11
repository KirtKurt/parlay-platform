import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import path from 'node:path';
const exec = promisify(execFile);
export async function git(cwd, args) { return (await exec('git', args, { cwd, maxBuffer: 10 * 1024 * 1024 })).stdout.trim(); }
export async function resolveRevision(repo, requested = 'HEAD') { return git(repo, ['rev-parse', '--verify', `${requested}^{commit}`]); }
export async function createWorkspace(config, job) { const branch = `inqsi/job-${job.id}`; const workspace = path.join(config.workspaceRoot, job.id); await exec('mkdir', ['-p', config.workspaceRoot]); await git(config.repository, ['worktree', 'add', '-b', branch, workspace, job.startingRevision]); return { branch, workspace }; }
export async function collectChanges(workspace) { const changedFiles = (await git(workspace, ['status', '--porcelain'])).split('\n').filter(Boolean).map((line) => line.slice(3)); const diff = await git(workspace, ['diff', '--no-ext-diff', '--binary']); return { changedFiles, diff }; }
